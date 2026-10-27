from django.core.management.base import BaseCommand, CommandError

from customer_manager.models import PaymentTransaction
from customer_manager.payment_service import (
    OPEN_PAYMENT_STATUSES,
    reconcile_open_transactions,
    reconcile_payment_transaction,
)


class Command(BaseCommand):
    help = '主动向支付服务商查单对账；查单失败不会把交易标成过期'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='只列出待对账交易，不查单')
        parser.add_argument('--transaction-no', default='', help='只对账指定交易号')
        parser.add_argument('--force', action='store_true', help='忽略 45 秒节流')
        parser.add_argument('--only-expired', action='store_true', help='只处理已过本地有效期的交易')

    def handle(self, *args, **options):
        transaction_no = str(options.get('transaction_no') or '').strip()
        if transaction_no:
            try:
                payment = PaymentTransaction.objects.get(transaction_no=transaction_no)
            except PaymentTransaction.DoesNotExist as exc:
                raise CommandError(f'找不到支付交易 {transaction_no}') from exc
            if options['dry_run']:
                self.stdout.write(
                    f'{payment.transaction_no} status={payment.status} '
                    f'expires_at={payment.expires_at}'
                )
                return
            result = reconcile_payment_transaction(
                payment,
                force=bool(options['force']),
                expire_if_unpaid=True,
            )
            self.stdout.write(self.style.SUCCESS(
                '{transaction_no} action={action} status={status} reason={reason}'.format(**result)
            ))
            return

        if options['dry_run']:
            qs = PaymentTransaction.objects.filter(status__in=OPEN_PAYMENT_STATUSES).order_by('id')
            if options['only_expired']:
                from django.utils import timezone
                qs = qs.filter(expires_at__lt=timezone.now())
            count = 0
            for payment in qs:
                count += 1
                self.stdout.write(
                    f'{payment.transaction_no} status={payment.status} '
                    f'expires_at={payment.expires_at}'
                )
            self.stdout.write(self.style.SUCCESS(f'待对账 {count} 笔'))
            return

        stats = reconcile_open_transactions(
            force=bool(options['force']),
            only_expired=bool(options['only_expired']),
        )
        self.stdout.write(self.style.SUCCESS(
            '支付对账完成：扫描 {scanned}，入账 {paid}，失败 {failed}，'
            '过期 {expired}，仍待付 {pending}，查单失败 {error}，跳过 {skipped}'.format(**stats)
        ))
