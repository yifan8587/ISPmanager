from django.core.management.base import BaseCommand

from customer_manager.payment_service import reconcile_open_transactions


class Command(BaseCommand):
    help = '向支付服务商查单对账；仍未付款且已过期的交易才标过期'

    def handle(self, *args, **options):
        stats = reconcile_open_transactions(only_expired=False)
        self.stdout.write(self.style.SUCCESS(
            '支付对账完成：扫描 {scanned}，入账 {paid}，失败 {failed}，'
            '过期 {expired}，仍待付 {pending}，查单失败 {error}，跳过 {skipped}'.format(**stats)
        ))
