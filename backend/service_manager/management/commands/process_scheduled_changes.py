from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from service_manager.change_scheduler import process_due_scheduled_changes


class Command(BaseCommand):
    help = '扫描已到期的预约变更（降配等）并落库规格；默认真执行'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='只列出到期变更，不写库',
        )
        parser.add_argument(
            '--now',
            dest='now_value',
            help='模拟当前时间，ISO 8601，如 2026-09-01T00:00:00',
        )
        parser.add_argument(
            '--no-ticket',
            action='store_true',
            help='本次不创建节点下发跟进工单（覆盖系统参数）',
        )

    def handle(self, *args, **options):
        now = timezone.now()
        raw_now = options.get('now_value')
        if raw_now:
            parsed = parse_datetime(raw_now)
            if parsed is None:
                raise CommandError('--now 必须为 ISO 8601 日期时间')
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            now = parsed

        create_ticket = False if options.get('no_ticket') else None
        stats = process_due_scheduled_changes(
            now=now,
            dry_run=bool(options.get('dry_run')),
            create_followup_ticket=create_ticket,
        )
        prefix = '[dry-run] ' if stats['dry_run'] else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}扫描 {stats["scanned"]} 条预约变更，'
            f'执行 {stats["executed"]}，'
            f'跳过 {stats["skipped"]}，'
            f'跟进工单 {stats["tickets"]}，'
            f'错误 {stats["errors"]}'
        ))
        for line in stats.get('planned') or []:
            self.stdout.write(f'  - {line}')
