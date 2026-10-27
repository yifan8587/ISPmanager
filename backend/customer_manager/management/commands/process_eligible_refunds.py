from django.core.management.base import BaseCommand

from customer_manager.refund_engine import scan_eligible_refunds


class Command(BaseCommand):
    help = '扫描开通失败 / 升配未执行的已付账单并尝试自动退款'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='只列出合格账单，不发起退款',
        )

    def handle(self, *args, **options):
        stats = scan_eligible_refunds(dry_run=options['dry_run'])
        prefix = '计划' if options['dry_run'] else '已处理'
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}：扫描 {stats["scanned"]}，退款 {stats["refunded"]}，'
            f'跳过 {stats["skipped"]}，错误 {stats["errors"]}',
        ))
        for item in stats.get('planned') or []:
            self.stdout.write(f'  - {item}')
