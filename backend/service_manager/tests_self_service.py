"""D3：门户重装 / 快照自助骨架。"""
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager.models import Customer
from product_manager.models import Product
from resource_manager.models import City, Country, IPAddress, IPBlock, PopPoint, Region, VPS
from service_manager.models import InstanceSnapshot, OsImage, SelfServiceJob, ServiceInstance, ServiceOrder
from system_manager.models import SystemOsTemplate
from service_manager.self_service import complete_job, ensure_default_os_images, request_reinstall, request_snapshot


class SelfServiceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('ss-admin', 'ss@example.test', 'password')
        self.user = User.objects.create_user('ss-portal', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='自助客户',
            contact_name='联系人',
            account=self.user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])

        region = Region.objects.create(code='ss', name='自助区')
        country = Country.objects.create(region=region, name='自助国')
        city = City.objects.create(country=country, name='自助市')
        self.pop = PopPoint.objects.create(name='自助 POP', city=city)
        self.product = Product.objects.create(
            name='自助 VPS',
            category='vps',
            status='online',
            pop_point=self.pop,
            monthly_price='10.00',
        )
        self.vps = VPS.objects.create(
            name='自助主机',
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            vcpu=1,
            ram_mb=1024,
            disk_gb=20,
            status='running',
            os_type='debian',
            os_version='11',
        )
        order = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            created_by=self.admin,
        )
        ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.VPS,
            status=ServiceInstance.InstanceStatus.ACTIVE,
            vps=self.vps,
        )
        ensure_default_os_images()
        self.image = OsImage.objects.get(code='ubuntu2404')

        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.portal = APIClient()
        self.portal.force_authenticate(self.user)

    def test_reinstall_job_then_complete_updates_os_and_wipes_password(self):
        job = request_reinstall(
            self.vps,
            os_image=self.image,
            password='NewRoot_2026',
            requested_by=self.user,
        )
        self.assertEqual(job.status, SelfServiceJob.Status.PENDING)
        self.assertEqual(job.request_payload.get('password'), 'NewRoot_2026')

        listed = self.admin_client.get(f'/api/service/self-service-jobs/{job.pk}/')
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn('password', listed.data['request_payload'])

        complete_job(job)
        job.refresh_from_db()
        self.vps.refresh_from_db()
        self.assertEqual(job.status, SelfServiceJob.Status.SUCCEEDED)
        self.assertNotIn('password', job.request_payload)
        self.assertTrue(job.request_payload.get('password_set'))
        self.assertEqual(self.vps.os_type, 'ubuntu')
        self.assertEqual(self.vps.os_version, '24.04')
        self.assertEqual(self.vps.mgmt_password, 'NewRoot_2026')

    def test_portal_reinstall_and_busy_lock(self):
        images = self.portal.get('/api/portal/os-images/')
        self.assertEqual(images.status_code, 200)
        self.assertTrue(images.data['data'])

        created = self.portal.post(f'/api/portal/vps/{self.vps.pk}/reinstall/', {
            'os_image': self.image.pk,
            'password': 'SafePass_2026',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        from resource_manager.models import ResourceOperationLog
        log = ResourceOperationLog.objects.filter(vps=self.vps, description__contains='重装').first()
        self.assertIsNotNone(log)
        self.assertTrue(log.new_value.get('password_set'))
        self.assertNotIn('SafePass_2026', str(log.new_value))

        again = self.portal.post(f'/api/portal/vps/{self.vps.pk}/reinstall/', {
            'os_image': self.image.pk,
        }, format='json')
        self.assertEqual(again.status_code, 400)

    def test_snapshot_create_restore_and_command_apply(self):
        created = self.portal.post(f'/api/portal/vps/{self.vps.pk}/snapshots/create/', {
            'name': '开机前',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        snap_id = created.data['data']['snapshot']['id']
        job_id = created.data['data']['job']['id']

        listed = self.portal.get(f'/api/portal/vps/{self.vps.pk}/snapshots/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data['data'][0]['status'], 'pending')

        restore_early = self.portal.post(
            f'/api/portal/vps/{self.vps.pk}/snapshots/{snap_id}/restore/',
            {},
            format='json',
        )
        self.assertEqual(restore_early.status_code, 400)

        out = StringIO()
        call_command('process_selfservice_jobs', stdout=out)
        snap = InstanceSnapshot.objects.get(pk=snap_id)
        self.assertEqual(snap.status, InstanceSnapshot.Status.PENDING)

        call_command('process_selfservice_jobs', '--apply', stdout=StringIO())
        snap.refresh_from_db()
        self.assertEqual(snap.status, InstanceSnapshot.Status.READY)
        SelfServiceJob.objects.get(pk=job_id).refresh_from_db()

        restored = self.portal.post(
            f'/api/portal/vps/{self.vps.pk}/snapshots/{snap_id}/restore/',
            {},
            format='json',
        )
        self.assertEqual(restored.status_code, 200, restored.data)
        snap.refresh_from_db()
        self.assertEqual(snap.status, InstanceSnapshot.Status.RESTORING)

        self.admin_client.post(f'/api/service/self-service-jobs/{restored.data["data"]["id"]}/complete/')
        snap.refresh_from_db()
        self.assertEqual(snap.status, InstanceSnapshot.Status.READY)
        self.assertIsNotNone(snap.restored_at)

    def test_reinstall_allows_vps_without_service_instance(self):
        orphan = VPS.objects.create(
            name='无实例主机',
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            vcpu=1,
            ram_mb=1024,
            disk_gb=20,
            status='running',
        )
        job = request_reinstall(orphan, os_image=self.image, requested_by=self.user)
        self.assertIsNone(job.service_instance_id)

    def test_admin_os_image_crud(self):
        resp = self.admin_client.post('/api/service/os-images/', {
            'code': 'win2022',
            'name': 'Windows Server 2022',
            'os_type': 'windows',
            'version': '2022',
            'enabled': True,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        listed = self.admin_client.get('/api/service/os-images/', {'search': 'win2022'})
        self.assertGreaterEqual(listed.data['count'], 1)

    def test_reinstall_ensures_cloud_image_and_passes_assigned_ip(self):
        from unittest.mock import patch

        catalog = SystemOsTemplate.objects.create(
            name='Ubuntu',
            version='24.04',
            backup_filename='noble-server-cloudimg-amd64.img',
            download_url='http://mirror.example.com/noble-server-cloudimg-amd64.img',
        )
        image = OsImage.objects.get(os_template=catalog)
        self.vps.source_ref = '55'
        self.vps.save(update_fields=['source_ref', 'updated_at'])
        block = IPBlock.objects.create(pop_point=self.pop, cidr='10.9.0.0/24', gateway='10.9.0.1')
        IPAddress.objects.create(
            ip_block=block,
            address='10.9.0.30',
            prefix_len=24,
            assigned_vps=self.vps,
            is_primary=True,
            status=IPAddress.IPStatus.ASSIGNED,
        )
        captured = {}

        def fake_reinstall(vps, payload, dry_run=False):
            captured['payload'] = payload
            return {'ok': True, 'status': 'succeeded', 'job_id': 're-1'}

        with patch('resource_manager.pemanager_client.pop_has_api_config', return_value=True), \
             patch('resource_manager.pve_ops.ensure_os_template_on_pop', return_value={'id': 21}) as ensure, \
             patch('resource_manager.pve_ops.reinstall_vps', side_effect=fake_reinstall):
            job = request_reinstall(
                self.vps,
                os_image=image,
                password='Reinstall_2026',
                requested_by=self.user,
                notify=False,
            )
        ensure.assert_called_once()
        self.assertEqual(job.status, SelfServiceJob.Status.SUCCEEDED)
        self.assertEqual(captured['payload']['template_id'], 21)
        self.assertEqual(captured['payload']['download_url'], catalog.download_url)
        self.assertEqual(captured['payload']['ip'], '10.9.0.30')
        self.assertEqual(int(captured['payload']['prefix']), 24)
        self.assertEqual(captured['payload']['gateway'], '10.9.0.1')
        self.assertEqual(captured['payload']['username'], 'ubuntu')
        self.assertEqual(captured['payload']['cores'], self.vps.vcpu)
        self.assertEqual(captured['payload']['memory_mb'], self.vps.ram_mb)
        self.assertEqual(captured['payload']['disk_gb'], self.vps.disk_gb)
