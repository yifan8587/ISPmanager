from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import transaction
from django.test import RequestFactory
from django.test import TestCase
from rest_framework.test import APIClient

from customer_manager import payment_gateways, payment_service
from customer_manager.models import Customer, CustomerBilling, PaymentTransaction
from portal.serializers import PortalOrderCreateSerializer
from product_manager.models import Product
from resource_manager.models import City, Country, IPAddress, IPBlock, PopPoint, Region, VPS
from system_manager.models import PaymentChannel

from .models import (
    ServiceAllocation, ServiceInquiry, ServiceInquiryReply,
    ServiceChangeRecord, ServiceInstance, ServiceOrder, json_safe,
)
from .payment_billing import ensure_order_payment_billing
from .capacity_recovery import retry_insufficient_paid_orders_for_pop
from .payment_fulfillment import fulfill_paid_billing, fulfill_paid_billing_safely


class PaymentProvisioningLoopTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            'service-admin',
            'service@example.test',
            'password',
        )
        self.customer = Customer.objects.create(
            name='闭环测试客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='apac', name='亚太')
        country = Country.objects.create(region=region, name='中国')
        city = City.objects.create(country=country, name='上海')
        self.pop = PopPoint.objects.create(
            name='手工 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.MANUAL,
        )
        self.product = Product.objects.create(
            name='USD VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('100.00'),
            currency='USD',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def create_order(self, quoted_price=Decimal('100.00')):
        return ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.PENDING,
            quoted_price=quoted_price,
            created_by=self.user,
        )

    def test_approval_creates_unique_order_prepayment_snapshot(self):
        order = self.create_order()
        response = self.client.post(
            f'/api/service/orders/{order.pk}/approve/',
            {'approved': True},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        billing = CustomerBilling.objects.get(service_order=order)
        self.assertEqual(billing.amount, Decimal('100.00'))
        self.assertEqual(billing.currency, 'USD')
        self.assertFalse(billing.auto_generated)
        self.assertEqual(billing.items.count(), 1)
        item = billing.items.get()
        self.assertEqual(item.instance_no, order.order_no)
        self.assertEqual(item.detail['billing_kind'], 'service_order_prepayment')
        self.assertEqual(item.detail['billing_cycle'], order.billing_cycle)

        self.assertEqual(ensure_order_payment_billing(order).pk, billing.pk)
        self.assertEqual(CustomerBilling.objects.filter(service_order=order).count(), 1)

    def test_approval_without_quote_is_blocked(self):
        order = self.create_order(quoted_price=None)
        response = self.client.post(
            f'/api/service/orders/{order.pk}/approve/',
            {'approved': True},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, ServiceOrder.OrderStatus.PENDING)
        self.assertFalse(CustomerBilling.objects.filter(service_order=order).exists())

    @patch('service_manager.views.ServiceOrderViewSet._try_auto_provision')
    def test_zero_quote_skips_billing_and_enters_provisioning(self, try_provision):
        order = self.create_order(quoted_price=Decimal('0.00'))
        response = self.client.post(
            f'/api/service/orders/{order.pk}/approve/',
            {'approved': True},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        try_provision.assert_called_once()
        self.assertFalse(CustomerBilling.objects.filter(service_order=order).exists())

    @patch('service_manager.provisioning.auto_provision')
    def test_paid_fulfillment_is_idempotent(self, auto_provision):
        order = self.create_order()
        order.status = ServiceOrder.OrderStatus.APPROVED
        order.auto_provision_status = ServiceOrder.AutoProvisionStatus.PAYMENT_PENDING
        order.save(update_fields=['status', 'auto_provision_status', 'updated_at'])
        billing = ensure_order_payment_billing(order)
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = billing.amount
        billing.save(update_fields=['status', 'paid_amount', 'updated_at'])
        auto_provision.return_value = {'auto': True, 'ok': True}

        first = fulfill_paid_billing(billing.pk)
        second = fulfill_paid_billing(billing.pk)
        self.assertTrue(first['triggered'])
        self.assertFalse(second['triggered'])
        auto_provision.assert_called_once()

    @patch('service_manager.provisioning.auto_provision', side_effect=RuntimeError('node failed'))
    def test_provision_failure_keeps_confirmed_payment(self, _auto_provision):
        order = self.create_order()
        order.status = ServiceOrder.OrderStatus.APPROVED
        order.save(update_fields=['status', 'updated_at'])
        billing = ensure_order_payment_billing(order)
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = billing.amount
        billing.save(update_fields=['status', 'paid_amount', 'updated_at'])

        fulfill_paid_billing_safely(billing.pk)
        billing.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(billing.status, CustomerBilling.BillingStatus.REFUNDING)
        self.assertEqual(billing.paid_amount, billing.amount)
        self.assertEqual(order.auto_provision_status, ServiceOrder.AutoProvisionStatus.FAILED)
        self.assertIn('node failed', order.auto_provision_message)

    @patch('service_manager.capacity_recovery.notify_managers')
    @patch('service_manager.capacity_recovery._check_resources')
    def test_capacity_recovery_notifies_manual_delivery_when_stock_returns(
        self,
        check_resources,
        notify,
    ):
        self.pop.config_mode = PopPoint.ConfigMode.AUTO
        self.pop.save(update_fields=['config_mode', 'updated_at'])
        order = self.create_order()
        order.status = ServiceOrder.OrderStatus.APPROVED
        order.auto_provision_status = ServiceOrder.AutoProvisionStatus.INSUFFICIENT
        order.save(update_fields=['status', 'auto_provision_status', 'updated_at'])
        billing = ensure_order_payment_billing(order)
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = billing.amount
        billing.save(update_fields=['status', 'paid_amount', 'updated_at'])
        check_resources.return_value = {'ok': True, 'shortages': []}

        result = retry_insufficient_paid_orders_for_pop(self.pop, reason='新增资源')

        self.assertEqual(result['checked'], 1)
        self.assertEqual(result['retried'], 0)
        self.assertEqual(result['ready_for_manual'], 1)
        notify.assert_called_once()
        order.refresh_from_db()
        self.assertIn('手动交付', order.auto_provision_message)
        self.assertFalse(order.instances.exists())

    @patch('service_manager.capacity_recovery.notify_managers')
    @patch('service_manager.capacity_recovery._check_resources')
    def test_capacity_recovery_does_not_realert_when_capacity_still_insufficient(
        self,
        check_resources,
        notify,
    ):
        self.pop.config_mode = PopPoint.ConfigMode.AUTO
        self.pop.save(update_fields=['config_mode', 'updated_at'])
        order = self.create_order()
        order.status = ServiceOrder.OrderStatus.APPROVED
        order.auto_provision_status = ServiceOrder.AutoProvisionStatus.INSUFFICIENT
        order.save(update_fields=['status', 'auto_provision_status', 'updated_at'])
        billing = ensure_order_payment_billing(order)
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = billing.amount
        billing.save(update_fields=['status', 'paid_amount', 'updated_at'])
        check_resources.return_value = {
            'ok': False,
            'shortages': ['IP 数量不足：需 1，剩余 0'],
        }

        result = retry_insufficient_paid_orders_for_pop(self.pop, reason='带宽调整')

        self.assertEqual(result['checked'], 1)
        self.assertEqual(result['retried'], 0)
        self.assertEqual(result['ready_for_manual'], 0)
        self.assertEqual(result['still_insufficient'], 1)
        notify.assert_not_called()

    @patch('service_manager.payment_fulfillment.fulfill_paid_billing_safely')
    def test_any_online_gateway_schedules_fulfillment_after_commit(self, fulfill):
        order = self.create_order()
        order.status = ServiceOrder.OrderStatus.APPROVED
        order.save(update_fields=['status', 'updated_at'])
        billing = ensure_order_payment_billing(order)
        channel = PaymentChannel.objects.create(
            provider='stripe',
            name='Stripe',
            enabled=False,
        )
        payment = PaymentTransaction.objects.create(
            billing=billing,
            channel=channel,
            provider='stripe',
            amount=billing.amount,
            currency=billing.currency,
            status=PaymentTransaction.Status.PENDING,
        )
        result = payment_gateways.GatewayResult(
            transaction_no=payment.transaction_no,
            paid_amount=billing.amount,
            paid_currency=billing.currency,
            succeeded=True,
        )
        with self.captureOnCommitCallbacks(execute=True):
            payment_service.apply_gateway_result(payment, result)
        fulfill.assert_called_once_with(billing.pk)


class ServiceRequestFlowTests(TestCase):
    """门户请求、人工交付、工单状态回写与咨询跟进的关键闭环。"""

    def setUp(self):
        self.user = User.objects.create_superuser(
            'flow-admin',
            'flow@example.test',
            'password',
        )
        self.handler = User.objects.create_user('service-handler', password='password')
        self.customer = Customer.objects.create(
            name='服务流程客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='flow-apac', name='流程亚太')
        country = Country.objects.create(region=region, name='流程国')
        city = City.objects.create(country=country, name='流程市')
        self.pop = PopPoint.objects.create(
            name='流程 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.MANUAL,
        )
        self.product = Product.objects.create(
            name='流程 VPS 产品',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('100.00'),
            currency='CNY',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _approved_order(self, business_type=ServiceOrder.BusinessType.IP, **kwargs):
        defaults = {
            'customer': self.customer,
            'product': self.product,
            'preferred_pop': self.pop,
            'order_type': ServiceOrder.OrderType.NEW,
            'business_type': business_type,
            'status': ServiceOrder.OrderStatus.APPROVED,
            'quoted_price': Decimal('0.00'),
            'created_by': self.user,
        }
        defaults.update(kwargs)
        return ServiceOrder.objects.create(**defaults)

    def test_portal_order_persists_quantity_total_quote_and_request_snapshot(self):
        request = RequestFactory().post('/api/portal/orders/')
        request.user = self.user
        serializer = PortalOrderCreateSerializer(
            data={
                'product_id': self.product.id,
                'quantity': 3,
                'cpu_cores': 2,
                'ram_mb': 2048,
                'disk_gb': 40,
                'bandwidth_mbps': 20,
                'ip_count': 1,
                'requirements': '三台同规格业务主机',
            },
            context={'customer': self.customer, 'request': request},
        )
        serializer.is_valid(raise_exception=True)
        order = serializer.save()

        self.assertEqual(order.requested_quantity, 3)
        self.assertEqual(order.quoted_price, Decimal('300.00'))
        self.assertEqual(order.request_snapshot['quantity'], 3)
        self.assertEqual(order.request_snapshot['requested_spec']['cpu_cores'], 2)
        self.assertEqual(order.request_snapshot['product']['product_no'], self.product.product_no)

    def test_manual_ip_delivery_updates_ip_instance_and_order_atomically(self):
        block = IPBlock.objects.create(pop_point=self.pop, cidr='198.51.100.0/24')
        ip = IPAddress.objects.create(
            ip_block=block,
            address='198.51.100.10',
            prefix_len=24,
            status=IPAddress.IPStatus.AVAILABLE,
        )
        order = self._approved_order(
            requested_ip_count=1,
            requested_quantity=1,
        )

        response = self.client.post(
            f'/api/service/orders/{order.pk}/deliver/',
            {'ip_ids': [ip.pk], 'activate': True, 'remark': '人工交付测试 IP'},
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        order.refresh_from_db()
        ip.refresh_from_db()
        instance = ServiceInstance.objects.get(service_order=order)
        self.assertEqual(order.status, ServiceOrder.OrderStatus.ACTIVE)
        self.assertEqual(instance.status, ServiceInstance.InstanceStatus.ACTIVE)
        self.assertEqual(ip.status, IPAddress.IPStatus.ASSIGNED)
        self.assertTrue(instance.ip_addresses.filter(pk=ip.pk).exists())
        self.assertTrue(ServiceAllocation.objects.filter(
            service_instance=instance,
            action_type=ServiceAllocation.ActionType.ALLOCATE,
        ).exists())

    def test_order_remains_active_until_all_instances_are_terminated(self):
        order = self._approved_order(
            business_type=ServiceOrder.BusinessType.IP,
            status=ServiceOrder.OrderStatus.ACTIVE,
            requested_quantity=2,
        )
        first = ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.IP_ADDRESS,
            status=ServiceInstance.InstanceStatus.ACTIVE,
        )
        second = ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.IP_ADDRESS,
            status=ServiceInstance.InstanceStatus.ACTIVE,
        )

        first.terminate(operator=self.user, reason='替换资源')
        order.refresh_from_db()
        self.assertEqual(order.status, ServiceOrder.OrderStatus.PROVISIONING)

        second.terminate(operator=self.user, reason='服务结束')
        order.refresh_from_db()
        self.assertEqual(order.status, ServiceOrder.OrderStatus.TERMINATED)

    def test_inquiry_reply_history_records_assignment_and_follow_up(self):
        inquiry = ServiceInquiry.objects.create(
            inquiry_type=ServiceInquiry.InquiryType.HELP,
            contact_name='官网访客',
            phone='13800138000',
            requirements='需要部署咨询',
        )

        assigned = self.client.post(
            f'/api/service/inquiries/{inquiry.pk}/assign/',
            {'handler': self.handler.pk},
            format='json',
        )
        self.assertEqual(assigned.status_code, 200, assigned.data)
        replied = self.client.post(
            f'/api/service/inquiries/{inquiry.pk}/reply/',
            {
                'entry_type': ServiceInquiryReply.EntryType.NOTE,
                'reply_content': '已电话沟通部署时间，等待客户确认。',
                'status': ServiceInquiry.InquiryStatus.PROCESSING,
            },
            format='json',
        )
        self.assertEqual(replied.status_code, 200, replied.data)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.status, ServiceInquiry.InquiryStatus.PROCESSING)
        self.assertEqual(inquiry.reply_records.count(), 2)
        self.assertEqual(inquiry.reply_records.last().content, '已电话沟通部署时间，等待客户确认。')
        self.assertEqual(replied.data['reply_count'], 2)

    def test_inquiry_can_be_converted_to_a_pending_service_order(self):
        inquiry = ServiceInquiry.objects.create(
            inquiry_type=ServiceInquiry.InquiryType.TRIAL,
            contact_name='官网测试客户',
            email='trial@example.test',
            requirements='需要测试上海 VPS 服务。',
        )

        response = self.client.post(
            f'/api/service/inquiries/{inquiry.pk}/convert-to-order/',
            {
                'customer': self.customer.pk,
                'product': self.product.pk,
                'handler': self.handler.pk,
                'requested_quantity': 2,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        inquiry.refresh_from_db()
        order = ServiceOrder.objects.get(pk=response.data['order_id'])
        self.assertEqual(order.source, ServiceOrder.OrderSource.INQUIRY)
        self.assertEqual(order.status, ServiceOrder.OrderStatus.PENDING)
        self.assertEqual(order.customer, self.customer)
        self.assertEqual(order.handler, self.handler)
        self.assertEqual(order.requested_quantity, 2)
        self.assertEqual(inquiry.related_order, order)
        self.assertEqual(inquiry.customer, self.customer)
        self.assertEqual(inquiry.status, ServiceInquiry.InquiryStatus.PROCESSING)
        self.assertEqual(
            inquiry.reply_records.last().entry_type,
            ServiceInquiryReply.EntryType.CONVERT,
        )
        self.assertEqual(order.request_snapshot['inquiry']['inquiry_no'], inquiry.inquiry_no)

    def test_vps_ip_allocation_binds_the_ip_to_its_instance_and_vps(self):
        from service_manager.provisioning import _allocate_order_ips

        block = IPBlock.objects.create(pop_point=self.pop, cidr='203.0.113.0/24')
        ip = IPAddress.objects.create(
            ip_block=block,
            address='203.0.113.10',
            prefix_len=24,
            status=IPAddress.IPStatus.AVAILABLE,
        )
        order = self._approved_order(
            business_type=ServiceOrder.BusinessType.VPS,
            requested_ip_count=1,
        )
        vps = VPS.objects.create(name='交付测试 VPS', pop_point=self.pop)
        instance = ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.VPS,
            status=ServiceInstance.InstanceStatus.PROVISIONING,
            vps=vps,
        )

        with transaction.atomic():
            allocated = _allocate_order_ips(order, self.pop, instance, vps=vps)

        self.assertEqual([item.pk for item in allocated], [ip.pk])
        ip.refresh_from_db()
        self.assertEqual(ip.status, IPAddress.IPStatus.ASSIGNED)
        self.assertEqual(ip.assigned_vps, vps)
        self.assertTrue(instance.ip_addresses.filter(pk=ip.pk).exists())

    def test_capacity_check_error_waits_for_manual_delivery(self):
        from service_manager.provisioning import auto_provision

        self.pop.config_mode = PopPoint.ConfigMode.AUTO
        self.pop.save(update_fields=['config_mode', 'updated_at'])
        order = self._approved_order(
            business_type=ServiceOrder.BusinessType.IP,
            requested_ip_count=1,
        )

        with patch(
            'service_manager.provisioning._check_resources',
            side_effect=RuntimeError('容量服务不可用'),
        ):
            result = auto_provision(order, operator=self.user)

        self.assertTrue(result['auto'])
        self.assertFalse(result['ok'])
        order.refresh_from_db()
        self.assertEqual(
            order.auto_provision_status,
            ServiceOrder.AutoProvisionStatus.INSUFFICIENT,
        )
        self.assertIn('容量服务不可用', order.auto_provision_message)


class ServiceOrderLogAndChangeFlowTests(TestCase):
    """工单更新日志序列化、变更审批/取消/支付守卫。"""

    def setUp(self):
        self.user = User.objects.create_superuser(
            'wf-admin',
            'wf@example.test',
            'password',
        )
        self.customer = Customer.objects.create(
            name='流转测试客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='wf-apac', name='流转亚太')
        country = Country.objects.create(region=region, name='流转国')
        city = City.objects.create(country=country, name='流转市')
        self.pop = PopPoint.objects.create(
            name='流转 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.MANUAL,
        )
        self.product = Product.objects.create(
            name='流转 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('80.00'),
            currency='CNY',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_json_safe_serializes_customer(self):
        payload = json_safe({'customer': self.customer, 'amount': Decimal('12.30')})
        self.assertEqual(payload['customer']['id'], self.customer.pk)
        self.assertEqual(payload['amount'], '12.30')

    def test_patch_order_with_customer_does_not_500(self):
        order = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.PENDING,
            quoted_price=Decimal('80.00'),
            created_by=self.user,
        )
        response = self.client.patch(
            f'/api/service/orders/{order.pk}/',
            {'customer': self.customer.pk, 'remark': '补客户字段'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.remark, '补客户字段')

    def _change_fixture(self, *, quoted_price=Decimal('50.00'), change_status='pending'):
        base_order = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.ACTIVE,
            quoted_price=Decimal('80.00'),
        )
        vps = VPS.objects.create(
            name='流转变更 VPS',
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            vcpu=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            status='running',
        )
        instance = ServiceInstance.objects.create(
            service_order=base_order,
            service_type='vps',
            status='active',
            vps=vps,
            monthly_fee=Decimal('80.00'),
            fee_currency='CNY',
        )
        related = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            order_type=ServiceOrder.OrderType.UPGRADE,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.PENDING,
            related_instance=instance,
            quoted_price=quoted_price,
            created_by=self.user,
        )
        change = ServiceChangeRecord.objects.create(
            service_instance=instance,
            related_order=related,
            change_type=ServiceChangeRecord.ChangeType.UPGRADE,
            status=change_status,
            change_reason='升配测试',
            after_spec={'vcpu': 2, 'ram_mb': 2048},
            price_delta=Decimal('20.00'),
        )
        return instance, related, change

    def test_change_approve_creates_payment_billing(self):
        _instance, related, change = self._change_fixture()
        response = self.client.post(
            f'/api/service/changes/{change.change_no}/approve/',
            {'status': 'approved'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        related.refresh_from_db()
        change.refresh_from_db()
        self.assertEqual(change.status, ServiceChangeRecord.ChangeStatus.APPROVED)
        self.assertEqual(related.status, ServiceOrder.OrderStatus.APPROVED)
        billing = CustomerBilling.objects.get(service_order=related)
        self.assertEqual(billing.amount, Decimal('50.00'))
        self.assertTrue(response.data['payment_required'])
        self.assertEqual(response.data['payment_billing']['bill_no'], billing.bill_no)

    def test_unpaid_change_cannot_execute(self):
        _instance, _related, change = self._change_fixture()
        self.client.post(
            f'/api/service/changes/{change.change_no}/approve/',
            {'status': 'approved'},
            format='json',
        )
        response = self.client.post(
            f'/api/service/changes/{change.change_no}/execute/',
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('尚未付清', response.data['detail'])

    def test_change_cancel_rejects_related_order(self):
        _instance, related, change = self._change_fixture()
        response = self.client.post(
            f'/api/service/changes/{change.change_no}/cancel/',
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        change.refresh_from_db()
        related.refresh_from_db()
        self.assertEqual(change.status, ServiceChangeRecord.ChangeStatus.CANCELLED)
        self.assertEqual(related.status, ServiceOrder.OrderStatus.REJECTED)
