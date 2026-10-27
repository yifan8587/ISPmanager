from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from customer_manager.models import CustomerBilling
from service_manager.models import ServiceOrder
from service_manager.payment_fulfillment import fulfill_paid_billing_safely


class Command(BaseCommand):
    help = '恢复已付款但尚未完成自动开通的专属预付账单'

    def handle(self, *args, **options):
        stale_before = timezone.now() - timedelta(minutes=10)
        billings = CustomerBilling.objects.filter(
            status=CustomerBilling.BillingStatus.PAID,
            service_order__status=ServiceOrder.OrderStatus.APPROVED,
            service_order__instances__isnull=True,
        ).filter(
            service_order__auto_provision_status__in=[
                ServiceOrder.AutoProvisionStatus.PAYMENT_PENDING,
                ServiceOrder.AutoProvisionStatus.PROVISIONING,
            ],
        ).distinct()

        recovered = 0
        for billing in billings.iterator():
            order = billing.service_order
            force = order.auto_provision_status == ServiceOrder.AutoProvisionStatus.PROVISIONING
            if (
                force
                and order.updated_at >= stale_before
            ):
                continue
            fulfill_paid_billing_safely(billing.pk, force=force)
            recovered += 1
        self.stdout.write(self.style.SUCCESS(f'已检查并触发 {recovered} 个已付款工单'))
