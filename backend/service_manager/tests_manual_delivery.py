"""D1：Socks5 等人开交付清单 + 回传 API。"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from customer_manager.models import Customer
from product_manager.models import Product
from resource_manager.models import (
    City, Country, EdgeAcceleration, PopPoint, Region, Socks5Config,
)
from service_manager.manual_delivery import delivery_for, ensure_manual_delivery
from service_manager.models import ServiceInstance, ServiceManualDelivery, ServiceOrder
from service_manager.provisioning import auto_provision


class ManualDeliveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('md-admin', 'md@example.test', 'password')
        self.customer = Customer.objects.create(
            name='人开交付客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='md', name='人开区')
        country = Country.objects.create(region=region, name='人开国')
        city = City.objects.create(country=country, name='人开市')
        self.auto_pop = PopPoint.objects.create(
            name='自动 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.AUTO,
        )
        self.manual_pop = PopPoint.objects.create(
            name='手动 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.MANUAL,
        )
        self.product = Product.objects.create(
            name='Socks5 产品',
            status=Product.Status.ONLINE,
            pop_point=self.auto_pop,
            monthly_price=Decimal('20.00'),
            currency='CNY',
            category='edge',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _order(self, **kwargs):
        defaults = {
            'customer': self.customer,
            'product': self.product,
            'preferred_pop': self.auto_pop,
            'order_type': ServiceOrder.OrderType.NEW,
            'business_type': ServiceOrder.BusinessType.EDGE_SOCKS5,
            'status': ServiceOrder.OrderStatus.APPROVED,
            'quoted_price': Decimal('0.00'),
            'created_by': self.user,
            'edge_socks5_port': 1080,
            'edge_socks5_user': 'proxy',
            'edge_socks5_pass': 'secret',
        }
        defaults.update(kwargs)
        return ServiceOrder.objects.create(**defaults)

    def test_socks5_creates_checklist_and_paid_pending(self):
        order = self._order()
        result = auto_provision(order, operator=self.user)
        self.assertFalse(result['auto'])
        self.assertEqual(result['reason'], 'socks5_manual_delivery')
        order.refresh_from_db()
        self.assertEqual(
            order.auto_provision_status,
            ServiceOrder.AutoProvisionStatus.PAID_PENDING,
        )
        delivery = delivery_for(order)
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.reason, ServiceManualDelivery.Reason.SOCKS5)
        self.assertEqual(len(delivery.items), 4)
        self.assertFalse(any(item['done'] for item in delivery.items))
        self.assertEqual(ServiceInstance.objects.filter(service_order=order).count(), 0)

    def test_ensure_is_idempotent(self):
        order = self._order()
        auto_provision(order, operator=self.user)
        first = delivery_for(order)
        second = ensure_manual_delivery(order, ServiceManualDelivery.Reason.SOCKS5)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ServiceManualDelivery.objects.filter(service_order=order).count(), 1)

    def test_multi_instance_and_bandwidth_and_manual_pop(self):
        multi = self._order(
            business_type=ServiceOrder.BusinessType.VPS,
            requested_quantity=3,
        )
        auto_provision(multi, operator=self.user)
        self.assertEqual(delivery_for(multi).reason, ServiceManualDelivery.Reason.MULTI_INSTANCE)

        bandwidth = self._order(business_type=ServiceOrder.BusinessType.BANDWIDTH)
        auto_provision(bandwidth, operator=self.user)
        self.assertEqual(delivery_for(bandwidth).reason, ServiceManualDelivery.Reason.BANDWIDTH)

        parked = self._order(
            business_type=ServiceOrder.BusinessType.VPS,
            preferred_pop=self.manual_pop,
        )
        result = auto_provision(parked, operator=self.user)
        self.assertEqual(result['reason'], 'pop_not_auto')
        parked.refresh_from_db()
        self.assertEqual(
            parked.auto_provision_status,
            ServiceOrder.AutoProvisionStatus.PAID_PENDING,
        )
        self.assertEqual(delivery_for(parked).reason, ServiceManualDelivery.Reason.POP_MANUAL)

    def test_token_callback_completes_socks5(self):
        order = self._order()
        auto_provision(order, operator=self.user)
        rotate = self.client.post(f'/api/service/orders/{order.pk}/manual-delivery/rotate-token/')
        self.assertEqual(rotate.status_code, 200, rotate.data)
        token = rotate.data['token']
        self.assertTrue(token.startswith('mdt_'))

        listed = self.client.get(f'/api/service/orders/{order.pk}/manual-delivery/')
        self.assertEqual(listed.status_code, 200)
        self.assertTrue(listed.data['exists'])
        self.assertNotIn('token', listed.data)
        self.assertEqual(listed.data['done_count'], 0)

        guest = APIClient()
        denied = guest.post(
            '/api/service/manual-delivery/callback/',
            {'token': 'mdt_invalid', 'complete': True},
            format='json',
        )
        self.assertEqual(denied.status_code, 401)

        ok = guest.post(
            '/api/service/manual-delivery/callback/',
            {
                'token': token,
                'note': '节点已配置',
                'node_pushed': True,
                'socks5': {
                    'create_edge': True,
                    'listen_port': 2080,
                    'username': 'u1',
                    'password': 'p1',
                },
                'complete': True,
            },
            format='json',
        )
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertTrue(ok.data['ok'])
        self.assertEqual(ok.data['status'], ServiceManualDelivery.Status.COMPLETED)
        self.assertEqual(ok.data['done_count'], 4)
        order.refresh_from_db()
        self.assertEqual(order.status, ServiceOrder.OrderStatus.ACTIVE)
        self.assertEqual(
            order.auto_provision_status,
            ServiceOrder.AutoProvisionStatus.SUCCESS,
        )
        instance = ServiceInstance.objects.get(service_order=order)
        self.assertEqual(instance.status, ServiceInstance.InstanceStatus.ACTIVE)
        self.assertEqual(instance.edge.protocol, EdgeAcceleration.Protocol.SOCKS5)
        self.assertTrue(instance.edge.node_pushed)
        self.assertEqual(instance.edge.socks5_config.listen_port, 2080)

        again = guest.post(
            '/api/service/manual-delivery/callback/',
            {'token': token, 'complete': True},
            format='json',
        )
        self.assertEqual(again.status_code, 200, again.data)
        self.assertTrue(again.data['already_completed'])
        self.assertEqual(ServiceInstance.objects.filter(service_order=order).count(), 1)

    def test_admin_deliver_closes_checklist(self):
        order = self._order()
        auto_provision(order, operator=self.user)
        edge = EdgeAcceleration.objects.create(
            name='预建 Socks5',
            protocol=EdgeAcceleration.Protocol.SOCKS5,
            status=EdgeAcceleration.EdgeStatus.PENDING,
            pop_point=self.auto_pop,
            customer=self.customer,
            node_pushed=True,
        )
        Socks5Config.objects.create(edge=edge, listen_port=1080, username='a', password='b')
        response = self.client.post(
            f'/api/service/orders/{order.pk}/deliver/',
            {'edge_id': edge.pk, 'activate': True, 'remark': '后台交付'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        delivery = delivery_for(order)
        self.assertEqual(delivery.status, ServiceManualDelivery.Status.COMPLETED)
        self.assertTrue(all(item['done'] for item in delivery.items))
        order.refresh_from_db()
        self.assertEqual(
            order.auto_provision_status,
            ServiceOrder.AutoProvisionStatus.SUCCESS,
        )

    def test_order_list_includes_manual_delivery_summary(self):
        order = self._order()
        auto_provision(order, operator=self.user)
        response = self.client.get(f'/api/service/orders/{order.pk}/')
        self.assertEqual(response.status_code, 200)
        summary = response.data['manual_delivery']
        self.assertEqual(summary['reason'], 'socks5')
        self.assertTrue(summary['open'])
        self.assertEqual(summary['total_count'], 4)

        from portal.serializers import PortalOrderSerializer
        portal = PortalOrderSerializer(order).data['manual_delivery']
        self.assertIsNotNone(portal)
        self.assertNotIn('token_prefix', portal)
        self.assertEqual(portal['done_count'], 0)

    def test_non_socks5_callback_cannot_complete_without_instances(self):
        order = self._order(
            business_type=ServiceOrder.BusinessType.VPS,
            preferred_pop=self.manual_pop,
        )
        auto_provision(order, operator=self.user)
        rotate = self.client.post(f'/api/service/orders/{order.pk}/manual-delivery/rotate-token/')
        token = rotate.data['token']
        response = APIClient().post(
            '/api/service/manual-delivery/callback/',
            {'token': token, 'mark': ['prepare_node'], 'complete': True},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('资源交付', response.data['detail'])
        delivery = delivery_for(order)
        self.assertTrue(any(item['key'] == 'prepare_node' and item['done'] for item in delivery.items))
        self.assertEqual(delivery.status, ServiceManualDelivery.Status.IN_PROGRESS)
