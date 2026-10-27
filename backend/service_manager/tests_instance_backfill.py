"""历史直连资源回填 ServiceInstance：列表只读，命令写库。"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager.models import Customer
from product_manager.models import Product
from resource_manager.models import City, Country, EdgeAcceleration, PopPoint, Region, VPS
from service_manager.instance_backfill import backfill_orphan_service_instances
from service_manager.models import ServiceInstance, ServiceOrder


class ServiceInstanceBackfillTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='bf-svc', name='回填测试区')
        country = Country.objects.create(region=region, name='回填测试国')
        city = City.objects.create(country=country, name='回填测试市')
        self.pop = PopPoint.objects.create(name='回填 POP', city=city)
        self.user = User.objects.create_user(username='backfill-portal', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='回填测试客户',
            contact_name='联系人',
            account=self.user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        self.product = Product.objects.create(
            name='回填产品',
            category='vps',
            status='online',
            pop_point=self.pop,
            cpu_cores=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            ip_count=1,
            monthly_price=Decimal('88.00'),
            setup_price=Decimal('0.00'),
            currency='CNY',
        )
        self.vps = VPS.objects.create(
            name='孤儿 VPS',
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            vcpu=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            status='running',
        )
        self.edge = EdgeAcceleration.objects.create(
            name='孤儿边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            bandwidth_mbps=50,
            status='active',
        )
        self.client = APIClient()
        self.user = User.objects.select_related('profile').get(pk=self.user.pk)
        self.client.force_authenticate(self.user)

    def test_portal_service_list_does_not_create_instances(self):
        before_instances = ServiceInstance.objects.count()
        before_orders = ServiceOrder.objects.count()

        response = self.client.get('/api/portal/services/')
        self.assertEqual(response.status_code, 200, response.data)
        results = response.data['results']
        self.assertEqual(len(results), 2)

        orphan_items = {item['id']: item for item in results}
        self.assertIn(f'vps:{self.vps.id}', orphan_items)
        self.assertIn(f'edge:{self.edge.id}', orphan_items)
        self.assertFalse(orphan_items[f'vps:{self.vps.id}']['can_change_request'])
        self.assertFalse(orphan_items[f'edge:{self.edge.id}']['can_change_request'])
        self.assertIsNone(orphan_items[f'vps:{self.vps.id}']['instance_id'])
        self.assertIsNone(orphan_items[f'edge:{self.edge.id}']['instance_id'])

        self.assertEqual(ServiceInstance.objects.count(), before_instances)
        self.assertEqual(ServiceOrder.objects.count(), before_orders)

    def test_dry_run_does_not_write(self):
        stats = backfill_orphan_service_instances(customer_id=self.customer.id, dry_run=True)
        self.assertEqual(stats['vps_found'], 1)
        self.assertEqual(stats['edge_found'], 1)
        self.assertEqual(stats['vps_created'], 0)
        self.assertEqual(stats['edge_created'], 0)
        self.assertEqual(ServiceInstance.objects.count(), 0)

    def test_command_backfills_and_is_idempotent(self):
        call_command('backfill_service_instances', customer_id=self.customer.id)
        self.assertEqual(ServiceInstance.objects.filter(vps=self.vps).count(), 1)
        self.assertEqual(ServiceInstance.objects.filter(edge=self.edge).count(), 1)
        instance = ServiceInstance.objects.get(vps=self.vps)
        self.assertEqual(instance.service_order.source, ServiceOrder.OrderSource.SYSTEM)
        self.assertEqual(instance.service_order.customer_id, self.customer.id)

        call_command('backfill_service_instances', customer_id=self.customer.id)
        self.assertEqual(ServiceInstance.objects.filter(vps=self.vps).count(), 1)
        self.assertEqual(ServiceInstance.objects.filter(edge=self.edge).count(), 1)
        self.assertEqual(ServiceOrder.objects.filter(source=ServiceOrder.OrderSource.SYSTEM).count(), 2)

        response = self.client.get('/api/portal/services/')
        self.assertEqual(response.status_code, 200, response.data)
        results = response.data['results']
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item['entry_type'] == 'instance' for item in results))
        self.assertTrue(all(item['can_change_request'] for item in results))
