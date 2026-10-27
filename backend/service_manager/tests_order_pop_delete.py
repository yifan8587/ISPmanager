from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from accounts.models import UserProfile
from backend.views import custom_exception_handler
from customer_manager.models import Customer, CustomerBilling
from product_manager.models import Product
from resource_manager.models import City, Country, EdgeAcceleration, PopPoint, Region, VPS

from .models import ServiceInstance, ServiceLifecycleRecord, ServiceOperationLog, ServiceOrder
from .order_pop import apply_product_fulfillment
from .payment_billing import ensure_order_payment_billing


class OrderPopAndDeleteTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            'order-pop-admin', 'order-pop@example.test', 'password',
        )
        self.staff = User.objects.create_user('order-pop-staff', password='password')
        profile, _ = UserProfile.objects.get_or_create(user=self.staff)
        profile.account_type = UserProfile.AccountType.SYSTEM
        profile.role = UserProfile.Role.OPERATOR
        profile.save(update_fields=['account_type', 'role', 'updated_at'])

        self.customer = Customer.objects.create(
            name='POP 履约客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='pop-apac', name='POP亚太')
        country = Country.objects.create(region=region, name='POP国')
        city = City.objects.create(country=country, name='POP市')
        self.pop = PopPoint.objects.create(
            name='产品 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.MANUAL,
            allow_oversell=True,
        )
        self.product = Product.objects.create(
            name='绑定 POP 的 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('50.00'),
            currency='USD',
            cpu_cores=2,
            ram_mb=2048,
            disk_gb=40,
            bandwidth_mbps=100,
            ip_count=1,
        )
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(self.staff)

    def _order(self, **kwargs):
        defaults = {
            'customer': self.customer,
            'product': self.product,
            'order_type': ServiceOrder.OrderType.NEW,
            'business_type': ServiceOrder.BusinessType.VPS,
            'status': ServiceOrder.OrderStatus.PENDING,
            'quoted_price': Decimal('50.00'),
            'created_by': self.admin,
        }
        defaults.update(kwargs)
        return ServiceOrder.objects.create(**defaults)

    def test_admin_create_order_inherits_product_pop_and_spec(self):
        response = self.admin_client.post(
            '/api/service/orders/',
            {
                'customer': self.customer.pk,
                'product': self.product.pk,
                'order_type': ServiceOrder.OrderType.NEW,
                'business_type': ServiceOrder.BusinessType.VPS,
                'quoted_price': '50.00',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        order = ServiceOrder.objects.get(customer=self.customer, product=self.product)
        self.assertEqual(order.preferred_pop_id, self.pop.pk)
        self.assertEqual(order.requested_vcpu, 2)
        self.assertEqual(order.requested_ram_mb, 2048)

    def test_delivery_options_fills_missing_preferred_pop(self):
        order = self._order(
            status=ServiceOrder.OrderStatus.APPROVED,
            preferred_pop=None,
            quoted_price=Decimal('0.00'),
        )
        response = self.admin_client.get(f'/api/service/orders/{order.pk}/delivery-options/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['preferred_pop_id'], self.pop.pk)
        self.assertEqual(response.data['product_pop_name'], self.pop.name)
        self.assertTrue(response.data['can_quick_provision'])
        order.refresh_from_db()
        self.assertEqual(order.preferred_pop_id, self.pop.pk)

    def test_quick_bootstrap_creates_vps_on_product_pop(self):
        order = self._order(
            status=ServiceOrder.OrderStatus.APPROVED,
            preferred_pop=None,
            quoted_price=Decimal('0.00'),
        )
        response = self.admin_client.post(
            f'/api/service/orders/{order.pk}/deliver/',
            {'quick_bootstrap': True, 'activate': False},
            format='json',
        )
        self.assertIn(response.status_code, (200, 201), response.data)
        order.refresh_from_db()
        self.assertEqual(order.preferred_pop_id, self.pop.pk)
        instance = order.instances.get()
        self.assertEqual(instance.vps.pop_point_id, self.pop.pk)
        self.assertEqual(instance.vps.vcpu, 2)
        self.assertEqual(VPS.objects.filter(pop_point=self.pop, service_instance=instance).count(), 1)

    def test_apply_product_fulfillment_repairs_empty_pop(self):
        order = self._order(preferred_pop=None, requested_vcpu=None)
        changed = apply_product_fulfillment(order)
        self.assertTrue(changed)
        order.refresh_from_db()
        self.assertEqual(order.preferred_pop_id, self.pop.pk)
        self.assertEqual(order.requested_vcpu, 2)

    def test_delete_missing_order_is_idempotent(self):
        response = self.admin_client.delete('/api/service/orders/999999/')
        self.assertEqual(response.status_code, 204, response.data)

    def test_superuser_can_delete_pending_order(self):
        order = self._order(status=ServiceOrder.OrderStatus.PENDING)
        response = self.admin_client.delete(f'/api/service/orders/{order.pk}/')
        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(ServiceOrder.objects.filter(pk=order.pk).exists())

    def test_superuser_can_delete_approved_order_without_instance(self):
        order = self._order(status=ServiceOrder.OrderStatus.APPROVED)
        response = self.admin_client.delete(f'/api/service/orders/{order.pk}/')
        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(ServiceOrder.objects.filter(pk=order.pk).exists())

    def test_superuser_can_delete_order_with_billing_logs_and_lifecycle(self):
        order = self._order(status=ServiceOrder.OrderStatus.APPROVED)
        billing = ensure_order_payment_billing(order)
        self.assertIsNotNone(billing)
        ServiceOperationLog.objects.create(
            action_type=ServiceOperationLog.ActionType.OTHER,
            description='已有日志',
            service_order=order,
        )
        ServiceLifecycleRecord.objects.create(
            service_order=order,
            stage=ServiceOrder.LifecycleStage.ORDER,
            status=ServiceLifecycleRecord.StageStatus.COMPLETED,
            title='下单',
        )
        response = self.admin_client.delete(f'/api/service/orders/{order.pk}/')
        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(ServiceOrder.objects.filter(pk=order.pk).exists())
        self.assertFalse(CustomerBilling.objects.filter(pk=billing.pk, service_order=order.pk).exists())
        leftover = CustomerBilling.objects.get(pk=billing.pk)
        self.assertIsNone(leftover.service_order_id)

    def test_staff_cannot_delete_pending_order_with_clear_message(self):
        order = self._order(status=ServiceOrder.OrderStatus.PENDING)
        response = self.staff_client.delete(f'/api/service/orders/{order.pk}/')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('只能删除草稿或已驳回的工单', response.data.get('message', ''))
        self.assertTrue(ServiceOrder.objects.filter(pk=order.pk).exists())

    def test_superuser_cannot_delete_order_with_instance(self):
        order = self._order(status=ServiceOrder.OrderStatus.APPROVED)
        vps = VPS.objects.create(
            name='已交付 VPS',
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
        )
        ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.VPS,
            status=ServiceInstance.InstanceStatus.ACTIVE,
            vps=vps,
        )
        response = self.admin_client.delete(f'/api/service/orders/{order.pk}/')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('已关联服务实例', response.data.get('message', ''))

    def test_permission_denied_custom_message_is_not_generic(self):
        factory = APIRequestFactory()
        request = factory.delete('/api/service/orders/1/')
        response = custom_exception_handler(
            PermissionDenied('只能删除草稿或已驳回的工单'),
            {'request': request, 'view': APIView()},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['message'], '只能删除草稿或已驳回的工单')


class EdgeQuickProvisionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            'edge-pop-admin', 'edge-pop@example.test', 'password',
        )
        self.customer = Customer.objects.create(
            name='边缘建档客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='edge-pop', name='边缘区')
        country = Country.objects.create(region=region, name='边缘国')
        city = City.objects.create(country=country, name='边缘市')
        self.pop = PopPoint.objects.create(
            name='边缘产品 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.MANUAL,
            allow_oversell=True,
        )
        self.product = Product.objects.create(
            name='Socks5 产品',
            status=Product.Status.ONLINE,
            category='edge',
            pop_point=self.pop,
            monthly_price=Decimal('20.00'),
            currency='USD',
            bandwidth_mbps=50,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_quick_bootstrap_creates_socks5_edge_on_product_pop(self):
        order = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.EDGE_SOCKS5,
            status=ServiceOrder.OrderStatus.APPROVED,
            quoted_price=Decimal('0.00'),
            edge_socks5_port=1080,
            edge_socks5_user='proxy',
            edge_socks5_pass='secret',
        )
        response = self.client.post(
            f'/api/service/orders/{order.pk}/deliver/',
            {'quick_bootstrap': True},
            format='json',
        )
        self.assertIn(response.status_code, (200, 201), response.data)
        order.refresh_from_db()
        self.assertEqual(order.preferred_pop_id, self.pop.pk)
        edge = EdgeAcceleration.objects.get(pop_point=self.pop, customer=self.customer)
        self.assertEqual(edge.protocol, EdgeAcceleration.Protocol.SOCKS5)
        self.assertEqual(edge.socks5_config.listen_port, 1080)
        self.assertEqual(order.instances.get().edge_id, edge.pk)
