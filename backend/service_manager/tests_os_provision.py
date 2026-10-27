from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import RequestFactory, SimpleTestCase, TestCase

from customer_manager.models import Customer
from portal.serializers import PortalOrderCreateSerializer
from product_manager.models import Product
from resource_manager import pve_ops
from resource_manager.models import City, Country, IPAddress, IPBlock, PopPoint, Region, VPS
from service_manager.models import ServiceOrder
from service_manager.provisioning import _provision_vps
from system_manager.models import SystemOsTemplate


class EnsureOsTemplateOnPopTests(SimpleTestCase):
    def test_downloads_when_missing_then_returns_registered_template(self):
        catalog = SimpleNamespace(
            name='Ubuntu',
            version='22.04',
            display_label='Ubuntu 22.04',
            backup_filename='ubuntu-22.04-server-cloudimg-amd64.img',
            download_url='http://mirror.example.com/ubuntu-22.04-server-cloudimg-amd64.img',
            source_type='cloud',
            code='ubuntu-2204',
        )
        downloaded = {
            'id': 9,
            'name': 'Ubuntu 22.04',
            'filename': 'ubuntu-22.04-server-cloudimg-amd64.img',
            'download_url': catalog.download_url,
            'host': 3,
            'node': 'pve',
            'source_type': 'cloud',
        }
        calls = []

        def list_templates():
            return [downloaded] if calls else []

        client = SimpleNamespace(
            list_templates=list_templates,
            list_pve_hosts=lambda: [{
                'id': 3,
                'default_node': 'pve',
                'nodes': [{'node': 'pve'}],
                'storages': [{'storage': 'local', 'content': 'backup,iso', 'node': 'pve'}],
            }],
            host_storages=lambda *args, **kwargs: [],
            download_template=lambda payload, timeout=2100: calls.append(payload) or {'upid': 'UPID:1'},
        )
        with patch.object(pve_ops, '_connect', return_value=(client, 'node-admin')):
            found = pve_ops.ensure_os_template_on_pop(SimpleNamespace(), catalog)
        self.assertEqual(found['id'], 9)
        self.assertEqual(calls[0]['url'], catalog.download_url)
        self.assertEqual(calls[0]['filename'], 'ubuntu-22.04-server-cloudimg-amd64.img')
        self.assertEqual(calls[0]['content'], 'iso')
        self.assertEqual(calls[0]['source_type'], 'cloud')
        self.assertTrue(calls[0]['wait'])
        self.assertFalse(calls[0]['via_manager'])

    def test_reuses_existing_template_without_download(self):
        catalog = SimpleNamespace(
            display_label='Debian 12',
            backup_filename='debian-12-genericcloud-amd64.qcow2',
            download_url='http://mirror.example.com/debian-12-genericcloud-amd64.qcow2',
            source_type='cloud',
            code='debian-12',
        )
        existing = {
            'id': 4,
            'name': 'Debian 12',
            'filename': 'debian-12-genericcloud-amd64.qcow2',
            'host': 1,
            'node': 'pve',
            'storage': 'local',
            'source_type': 'cloud',
            'volume': 'local:iso/debian-12-genericcloud-amd64.qcow2',
        }
        client = SimpleNamespace(
            list_templates=lambda: [existing],
            download_template=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('should not download')),
            host_storage_content=lambda *args, **kwargs: [{'volid': 'local:iso/debian-12-genericcloud-amd64.qcow2'}],
            host_backups=lambda *args, **kwargs: [],
        )
        with patch.object(pve_ops, '_connect', return_value=(client, 'node-admin')):
            found = pve_ops.ensure_os_template_on_pop(SimpleNamespace(), catalog)
        self.assertEqual(found['id'], 4)


class ProvisionVpsOsTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('prov-os', 'prov-os@example.test', 'password')
        self.customer = Customer.objects.create(
            name='开通系统模版客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='prov-os', name='开通模版区')
        country = Country.objects.create(region=region, name='开通模版国')
        city = City.objects.create(country=country, name='开通模版市')
        self.pop = PopPoint.objects.create(
            name='开通模版 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.AUTO,
        )
        self.product = Product.objects.create(
            name='开通模版 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            ip_count=0,
        )
        self.template = SystemOsTemplate.objects.create(
            name='Ubuntu',
            version='22.04',
            backup_filename='ubuntu-22.04-server-cloudimg-amd64.img',
            download_url='http://mirror.example.com/ubuntu-22.04-server-cloudimg-amd64.img',
        )
        self.order = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.APPROVED,
            requested_vcpu=2,
            requested_ram_mb=2048,
            requested_disk_gb=40,
            requested_os='Ubuntu 22.04',
            requested_os_template=self.template,
        )

    def test_provision_downloads_missing_template_then_creates_vm(self):
        node_template = {
            'id': 11,
            'name': 'Ubuntu 22.04',
            'filename': 'ubuntu-22.04-server-cloudimg-amd64.img',
        }
        created = []

        def fake_provision(pop, payload, dry_run=False):
            created.append(payload)
            VPS.objects.create(
                name=payload['name'],
                pop_point=pop,
                source_ref=str(payload.get('vmid') or 201),
            )
            return {
                'ok': True, 'status': 'succeeded',
                'result': {'vmid': payload.get('vmid'), 'vm_pk': 99},
            }

        with patch('resource_manager.pve_ops.list_pve_resources', return_value={
            'hosts': [{'id': 3, 'nodes': [{'node': 'pve'}], 'storages': [{'storage': 'local-lvm'}]}],
            'next_vmid': 201,
            'templates': [],
        }), patch('resource_manager.pve_ops.ensure_os_template_on_pop', return_value=node_template) as ensure, \
             patch('resource_manager.pve_ops.provision_vps', side_effect=fake_provision), \
             patch('resource_manager.pve_ops.create_vm') as create_empty:
            instance, message = _provision_vps(self.order, self.pop, self.user)

        ensure.assert_called_once()
        create_empty.assert_not_called()
        self.assertEqual(created[0]['template_id'], 11)
        self.assertEqual(created[0]['os_code'], self.template.code)
        self.assertEqual(created[0]['download_url'], self.template.download_url)
        self.assertEqual(created[0]['newname'], created[0]['name'])
        self.assertTrue(created[0]['password'])
        self.assertEqual(created[0]['username'], 'ubuntu')
        self.assertNotIn('ip', created[0])
        self.assertNotIn('gateway', created[0])
        self.assertNotIn('prefix', created[0])
        instance.vps.refresh_from_db()
        self.assertEqual(instance.vps.os_type, 'ubuntu')
        self.assertEqual(instance.vps.os_version, '22.04')
        self.assertEqual(instance.vps.mgmt_password, created[0]['password'])
        self.assertEqual(instance.vps.source_ref, '99')
        self.assertIn('已在节点创建虚机', message)

    def test_provision_uses_order_cloud_init_and_bandwidth(self):
        self.order.request_snapshot = {
            'cloud_init': {
                'username': 'portaluser',
                'password': 'CustomerPass_2026!',
                'sshkeys': 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest',
                'nameserver': '1.1.1.1',
            },
        }
        self.order.requested_bandwidth_mbps = 80
        self.order.save(update_fields=['request_snapshot', 'requested_bandwidth_mbps'])
        created = []

        def fake_provision(pop, payload, dry_run=False):
            created.append(payload)
            VPS.objects.create(name=payload['name'], pop_point=pop, source_ref='88')
            return {'ok': True, 'status': 'succeeded', 'result': {'vmid': 202, 'vm_pk': 88}}

        with patch('resource_manager.pve_ops.list_pve_resources', return_value={
            'hosts': [{'id': 3, 'nodes': [{'node': 'pve'}], 'storages': [{'storage': 'local-lvm'}]}],
            'next_vmid': 202,
            'templates': [],
        }), patch('resource_manager.pve_ops.ensure_os_template_on_pop', return_value={'id': 11}), \
             patch('resource_manager.pve_ops.provision_vps', side_effect=fake_provision):
            instance, _message = _provision_vps(self.order, self.pop, self.user)

        self.assertEqual(created[0]['username'], 'portaluser')
        self.assertEqual(created[0]['password'], 'CustomerPass_2026!')
        self.assertEqual(created[0]['sshkeys'], 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest')
        self.assertEqual(created[0]['cloud_init']['nameserver'], '1.1.1.1')
        self.assertEqual(created[0]['nics'][0]['rate_mb'], 10.0)
        instance.vps.refresh_from_db()
        self.assertEqual(instance.vps.mgmt_username, 'portaluser')
        self.assertEqual(instance.vps.mgmt_password, 'CustomerPass_2026!')

    def test_provision_writes_free_ip_cidr_gateway_to_cloud_init(self):
        block = IPBlock.objects.create(
            pop_point=self.pop,
            cidr='10.8.0.0/24',
            gateway='10.8.0.1',
        )
        IPAddress.objects.create(
            ip_block=block,
            address='10.8.0.20',
            prefix_len=24,
            status=IPAddress.IPStatus.AVAILABLE,
        )
        self.order.requested_ip_count = 1
        self.order.save(update_fields=['requested_ip_count'])
        created = []

        def fake_provision(pop, payload, dry_run=False):
            created.append(payload)
            VPS.objects.create(name=payload['name'], pop_point=pop, source_ref='77')
            return {'ok': True, 'status': 'succeeded', 'result': {'vmid': 203, 'vm_pk': 77}}

        with patch('resource_manager.pve_ops.list_pve_resources', return_value={
            'hosts': [{'id': 3, 'nodes': [{'node': 'pve'}], 'storages': [{'storage': 'local-lvm'}]}],
            'next_vmid': 203,
            'templates': [],
        }), patch('resource_manager.pve_ops.ensure_os_template_on_pop', return_value={'id': 11}), \
             patch('resource_manager.pve_ops.provision_vps', side_effect=fake_provision):
            _provision_vps(self.order, self.pop, self.user)

        cloud_init = created[0]['cloud_init']
        self.assertEqual(created[0]['ip'], '10.8.0.20')
        self.assertEqual(cloud_init['ip'], '10.8.0.20')
        self.assertEqual(int(cloud_init['prefix']), 24)
        self.assertEqual(cloud_init['gateway'], '10.8.0.1')

    def test_provision_materializes_ip_from_block_when_pool_empty(self):
        self.product.ip_count = 1
        self.product.save(update_fields=['ip_count'])
        self.order.requested_ip_count = 1
        self.order.save(update_fields=['requested_ip_count'])
        IPBlock.objects.create(
            pop_point=self.pop,
            cidr='10.10.0.0/28',
            gateway='10.10.0.1',
        )
        created = []

        def fake_provision(pop, payload, dry_run=False):
            created.append(payload)
            VPS.objects.create(name=payload['name'], pop_point=pop, source_ref='76')
            return {'ok': True, 'status': 'succeeded', 'result': {'vmid': 204, 'vm_pk': 76}}

        with patch('resource_manager.pve_ops.list_pve_resources', return_value={
            'hosts': [{'id': 3, 'nodes': [{'node': 'pve'}], 'storages': [{'storage': 'local-lvm'}]}],
            'next_vmid': 204,
            'templates': [],
        }), patch('resource_manager.pve_ops.ensure_os_template_on_pop', return_value={'id': 11}), \
             patch('resource_manager.pve_ops.provision_vps', side_effect=fake_provision):
            instance, message = _provision_vps(self.order, self.pop, self.user)

        self.assertEqual(created[0]['ip'], '10.10.0.2')
        self.assertEqual(created[0]['gateway'], '10.10.0.1')
        self.assertEqual(int(created[0]['prefix']), 28)
        self.assertIn('10.10.0.2', message)
        self.assertTrue(instance.vps.ip_addresses.filter(address='10.10.0.2').exists())

    def test_provision_requires_ip_pool_when_product_asks_for_ip(self):
        self.order.requested_ip_count = 1
        self.order.save(update_fields=['requested_ip_count'])
        with patch('resource_manager.pve_ops.list_pve_resources', return_value={
            'hosts': [{'id': 3, 'nodes': [{'node': 'pve'}], 'storages': [{'storage': 'local-lvm'}]}],
            'next_vmid': 205,
            'templates': [],
        }), patch('resource_manager.pve_ops.ensure_os_template_on_pop', return_value={'id': 11}):
            with self.assertRaisesMessage(RuntimeError, '可用 IP 不足'):
                _provision_vps(self.order, self.pop, self.user)


class PortalOrderOsTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('portal-os', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='门户系统模版客户',
            contact_name='联系人',
            account=self.user,
            status='active',
        )
        region = Region.objects.create(code='portal-os', name='门户模版区')
        country = Country.objects.create(region=region, name='门户模版国')
        city = City.objects.create(country=country, name='门户模版市')
        self.pop = PopPoint.objects.create(name='门户模版 POP', city=city)
        self.product = Product.objects.create(
            name='门户模版 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
        )
        self.template = SystemOsTemplate.objects.create(
            name='Debian',
            version='12',
            backup_filename='debian-12-genericcloud-amd64.qcow2',
            download_url='http://mirror.example.com/debian-12-genericcloud-amd64.qcow2',
        )

    def test_checkout_binds_os_template(self):
        request = RequestFactory().post('/api/portal/orders/')
        request.user = self.user
        serializer = PortalOrderCreateSerializer(
            data={
                'product_id': self.product.pk,
                'os_template_id': self.template.pk,
                'quantity': 1,
            },
            context={'request': request, 'customer': self.customer},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        order = serializer.save()
        self.assertEqual(order.requested_os_template_id, self.template.pk)
        self.assertEqual(order.requested_os, 'Debian 12')
        self.assertEqual(order.request_snapshot['os_template_id'], self.template.pk)

    def test_checkout_stores_cloud_init_credentials(self):
        request = RequestFactory().post('/api/portal/orders/')
        request.user = self.user
        serializer = PortalOrderCreateSerializer(
            data={
                'product_id': self.product.pk,
                'os_template_id': self.template.pk,
                'username': 'debian',
                'password': 'PortalPass_2026!',
                'sshkeys': 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest',
                'nameserver': '8.8.8.8 1.1.1.1',
            },
            context={'request': request, 'customer': self.customer},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        order = serializer.save()
        cloud_init = order.request_snapshot['cloud_init']
        self.assertEqual(cloud_init['username'], 'debian')
        self.assertEqual(cloud_init['password'], 'PortalPass_2026!')
        self.assertIn('ssh-ed25519', cloud_init['sshkeys'])
        self.assertEqual(cloud_init['nameserver'], '8.8.8.8 1.1.1.1')
