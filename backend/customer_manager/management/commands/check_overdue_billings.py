from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from customer_manager.billing_engine import mark_overdue_billings


class Command(BaseCommand):
    help = '将到期日前仍未结清的客户账单标记为逾期，并触发配置的逾期通知规则'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            dest='date_value',
            help='按指定日期检查，格式 YYYY-MM-DD；默认使用系统本地日期',
        )

    def handle(self, *args, **options):
        date_value = options.get('date_value')
        if date_value:
            try:
                check_date = date.fromisoformat(date_value)
            except ValueError as exc:
                raise ValueError('--date 必须为 YYYY-MM-DD 格式') from exc
        else:
            check_date = timezone.localdate()
        count = mark_overdue_billings(check_date)
        self.stdout.write(self.style.SUCCESS(
            f'已标记 {count} 笔逾期账单（检查日期：{check_date.isoformat()}）',
        ))
