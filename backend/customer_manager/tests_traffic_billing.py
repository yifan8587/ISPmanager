"""C3：流量归档生成账单明细。"""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from customer_manager import billing_engine
from customer_manager.models import BillingItem, Customer, CustomerBilling
from customer_manager.traffic_billing import (
    archive_closed_traffic_reports,
    generate_traffic_billing_item,
    process_traffic_billing_cycle,
    scan_archived_traffic_reports,
)
from operation_manager.models import MonitoringTrafficReport
from product_manager.models import Product
from resource_manager.models import City, Country, EdgeAcceleration, PopPoint, Region, VPS
from service_manager.models import ServiceInstance, ServiceOrder


class TrafficBillingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('tb-admin', 'tb@example.test', 'password')
        self.customer = Customer.objects.create(
            name='流量出账客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='tb', name='流量区')
        country = Country.objects.create(region=region, name='流量国')
        city = City.objects.create(country=country, name='流量市')
        self.pop = PopPoint.objects.create(name='流量 POP', city=city)
        self.product = Product.objects.create(
            name='95 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('88.00'),
            currency='CNY',
            cpu_cores=1,
            bandwidth_mbps=10,
            billing_mode=Product.BillingMode.P95,
            overage_unit_price=Decimal('1.2500'),
        )
        self.vps = VPS.objects.create(
            name='流量 VPS',
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            bandwidth_mbps=100,
        )

    def _window(self):
        return (
            timezone.make_aware(datetime.datetime(2026, 7, 1, 0, 0)),
            timezone.make_aware(datetime.datetime(2026, 7, 31, 23, 59)),
        )

    def _report(self, **kwargs):
        time_from, time_till = self._window()
        defaults = {
            'title': '七月流量',
            'target_type': 'vps',
            'vps': self.vps,
            'time_from': time_from,
            'time_till': time_till,
            'percentile_bps': Decimal('30000000'),
            'avg_bps': Decimal('20000000'),
            'total_in_bytes': Decimal(3 * 1024 ** 3),
            'total_out_bytes': Decimal(2 * 1024 ** 3),
            'status': MonitoringTrafficReport.ReportStatus.DONE,
        }
        defaults.update(kwargs)
        return MonitoringTrafficReport.objects.create(**defaults)

    def test_p95_overage_creates_separate_bill(self):
        report = self._report()
        result = generate_traffic_billing_item(report)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['amount'], '25.00')
        item = BillingItem.objects.get(traffic_report=report)
        self.assertEqual(item.amount, Decimal('25.00'))
        self.assertEqual(item.detail['overage_qty'], '20.0000')
        billing = item.billing
        self.assertEqual(billing.billing_kind, CustomerBilling.BillingKind.TRAFFIC_OVERAGE)
        self.assertEqual(billing.bill_month, '2026-07')
        self.assertTrue(billing.auto_generated)
        self.assertEqual(billing.amount, Decimal('25.00'))
        self.assertEqual(CustomerBilling.objects.filter(billing_kind='monthly').count(), 0)

    def test_under_commit_skips(self):
        report = self._report(percentile_bps=Decimal('5000000'))
        result = generate_traffic_billing_item(report)
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'no_overage')
        self.assertEqual(BillingItem.objects.count(), 0)

    def test_flat_skips(self):
        self.product.billing_mode = Product.BillingMode.FLAT
        self.product.save(update_fields=['billing_mode', 'updated_at'])
        report = self._report()
        result = generate_traffic_billing_item(report)
        self.assertEqual(result['reason'], 'billing_mode_flat')
        self.assertEqual(BillingItem.objects.count(), 0)

    def test_idempotent(self):
        report = self._report()
        first = generate_traffic_billing_item(report)
        second = generate_traffic_billing_item(report)
        self.assertEqual(first['item_id'], second['item_id'])
        self.assertEqual(second['reason'], 'already_billed')
        self.assertEqual(BillingItem.objects.count(), 1)
        self.assertEqual(CustomerBilling.objects.count(), 1)

    def test_avg_and_transfer_quota(self):
        self.product.billing_mode = Product.BillingMode.AVG
        self.product.save(update_fields=['billing_mode', 'updated_at'])
        avg_report = self._report()
        result = generate_traffic_billing_item(avg_report)
        self.assertEqual(result['amount'], '12.50')

        self.product.billing_mode = Product.BillingMode.TRANSFER_QUOTA
        self.product.transfer_quota_gb = 4
        self.product.overage_unit_price = Decimal('0.0200')
        self.product.save(update_fields=[
            'billing_mode', 'transfer_quota_gb', 'overage_unit_price', 'updated_at',
        ])
        quota_report = self._report(title='流量包')
        result = generate_traffic_billing_item(quota_report)
        self.assertEqual(result['amount'], '0.02')
        self.assertEqual(BillingItem.objects.get(traffic_report=quota_report).detail['overage_qty'], '1.0000')

    def test_pop_only_skips(self):
        time_from, time_till = self._window()
        report = MonitoringTrafficReport.objects.create(
            title='POP 报告',
            target_type='pop',
            pop_point=self.pop,
            time_from=time_from,
            time_till=time_till,
            percentile_bps=Decimal('30000000'),
            status=MonitoringTrafficReport.ReportStatus.DONE,
        )
        result = generate_traffic_billing_item(report)
        self.assertEqual(result['reason'], 'no_customer')

    def test_paid_traffic_bill_opens_new_one(self):
        first = self._report()
        generate_traffic_billing_item(first)
        billing = CustomerBilling.objects.get()
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = billing.amount
        billing.save(update_fields=['status', 'paid_amount', 'updated_at'])

        second = self._report(title='第二份')
        result = generate_traffic_billing_item(second)
        self.assertTrue(result['ok'], result)
        self.assertEqual(CustomerBilling.objects.filter(billing_kind='traffic_overage').count(), 2)
        self.assertNotEqual(result['billing_id'], billing.pk)

    def test_generate_bill_does_not_touch_traffic_bill(self):
        report = self._report()
        generate_traffic_billing_item(report)
        traffic_bill = CustomerBilling.objects.get(billing_kind='traffic_overage')

        with patch('customer_manager.billing_engine.compute_customer_month') as compute:
            compute.return_value = {
                'currency': 'CNY',
                'gross_total': '80.00',
                'billable_total': '80.00',
                'items': [],
            }
            monthly, _, updated = billing_engine.generate_bill(self.customer, '2026-07')
        self.assertTrue(updated)
        self.assertEqual(monthly.billing_kind, CustomerBilling.BillingKind.MONTHLY)
        self.assertNotEqual(monthly.pk, traffic_bill.pk)
        traffic_bill.refresh_from_db()
        self.assertEqual(traffic_bill.amount, Decimal('25.00'))
        self.assertEqual(traffic_bill.items.count(), 1)

    def test_order_snapshot_overrides_product(self):
        order = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.APPROVED,
            quoted_price=Decimal('88.00'),
            created_by=self.user,
            request_snapshot={
                'quote': {
                    'billing': {
                        'billing_mode': '95th',
                        'billing_mode_label': '95 计费',
                        'overage_unit_price': '2.0000',
                        'overage_unit_label': 'Mbps',
                        'commit_mbps': 10,
                        'transfer_quota_gb': None,
                    },
                },
            },
        )
        ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.VPS,
            vps=self.vps,
            status=ServiceInstance.InstanceStatus.ACTIVE,
        )
        report = self._report()
        result = generate_traffic_billing_item(report)
        self.assertEqual(result['amount'], '40.00')

    def test_archive_api_bills_and_command_dry_run(self):
        report = self._report()
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(f'/api/operation/traffic-reports/{report.pk}/archive/')
        self.assertEqual(response.status_code, 200, response.data)
        report.refresh_from_db()
        self.assertEqual(report.status, MonitoringTrafficReport.ReportStatus.ARCHIVED)
        self.assertEqual(response.data['traffic_billing']['status'], 'billed')
        self.assertEqual(response.data['traffic_billing']['amount'], '25.00')

        extra = self._report(title='待补扫', status=MonitoringTrafficReport.ReportStatus.ARCHIVED)
        stats = scan_archived_traffic_reports(dry_run=True)
        self.assertGreaterEqual(stats['scanned'], 1)
        self.assertEqual(BillingItem.objects.filter(traffic_report=extra).count(), 0)

        call_command('generate_traffic_billing_items')
        self.assertTrue(BillingItem.objects.filter(traffic_report=extra).exists())

    def test_edge_avg_overage(self):
        self.product.billing_mode = Product.BillingMode.AVG
        self.product.category = 'edge'
        self.product.save(update_fields=['billing_mode', 'category', 'updated_at'])
        edge = EdgeAcceleration.objects.create(
            name='流量边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
        )
        time_from, time_till = self._window()
        report = MonitoringTrafficReport.objects.create(
            title='边缘平均',
            target_type='edge',
            edge=edge,
            time_from=time_from,
            time_till=time_till,
            avg_bps=Decimal('20000000'),
            status=MonitoringTrafficReport.ReportStatus.DONE,
        )
        result = generate_traffic_billing_item(report)
        self.assertEqual(result['amount'], '12.50')
        self.assertEqual(BillingItem.objects.get(traffic_report=report).service_type, 'edge')

    def _current_month_window(self):
        now = timezone.localtime()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        till = now - datetime.timedelta(minutes=5)
        if till < start:
            till = start
        return start, till

    def test_auto_archive_previous_month_then_bill(self):
        report = self._report()
        self.assertEqual(report.status, MonitoringTrafficReport.ReportStatus.DONE)

        dry = process_traffic_billing_cycle(dry_run=True)
        report.refresh_from_db()
        self.assertEqual(report.status, MonitoringTrafficReport.ReportStatus.DONE)
        self.assertGreaterEqual(dry['archived'], 1)
        self.assertEqual(BillingItem.objects.filter(traffic_report=report).count(), 0)

        stats = process_traffic_billing_cycle()
        report.refresh_from_db()
        self.assertEqual(report.status, MonitoringTrafficReport.ReportStatus.ARCHIVED)
        self.assertGreaterEqual(stats['archived'], 1)
        self.assertTrue(BillingItem.objects.filter(traffic_report=report).exists())

    def test_current_month_done_is_not_auto_archived(self):
        time_from, time_till = self._current_month_window()
        report = self._report(
            title='本月未关账',
            time_from=time_from,
            time_till=time_till,
            status=MonitoringTrafficReport.ReportStatus.DONE,
        )
        stats = archive_closed_traffic_reports()
        report.refresh_from_db()
        self.assertEqual(report.status, MonitoringTrafficReport.ReportStatus.DONE)
        self.assertEqual(
            MonitoringTrafficReport.objects.filter(
                pk=report.pk,
                status=MonitoringTrafficReport.ReportStatus.ARCHIVED,
            ).count(),
            0,
        )
        self.assertGreaterEqual(stats['scanned'], 0)

        opened = archive_closed_traffic_reports(include_open_month=True)
        report.refresh_from_db()
        if time_till < timezone.localtime():
            self.assertEqual(report.status, MonitoringTrafficReport.ReportStatus.ARCHIVED)
            self.assertGreaterEqual(opened['archived'], 1)
        else:
            self.assertEqual(report.status, MonitoringTrafficReport.ReportStatus.DONE)

    def test_pending_report_is_not_auto_archived(self):
        report = self._report(status=MonitoringTrafficReport.ReportStatus.PENDING)
        archive_closed_traffic_reports()
        report.refresh_from_db()
        self.assertEqual(report.status, MonitoringTrafficReport.ReportStatus.PENDING)

    def test_command_archives_closed_reports(self):
        report = self._report()
        call_command('generate_traffic_billing_items')
        report.refresh_from_db()
        self.assertEqual(report.status, MonitoringTrafficReport.ReportStatus.ARCHIVED)
        self.assertTrue(BillingItem.objects.filter(traffic_report=report).exists())
