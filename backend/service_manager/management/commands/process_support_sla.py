from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from service_manager.support_ticket import process_support_sla


class Command(BaseCommand):
    help = (
        '扫描技术工单 SLA：响应/解决超时打标并通知；'
        '仅在指定 --apply-escalate 时提升 escalation_level，不自动改派'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='只报告将要执行的动作，不写库、不通知、不升级',
        )
        parser.add_argument(
            '--apply-escalate',
            action='store_true',
            help='解决超时且尚未升级的工单提升一级（默认不升级）',
        )
        parser.add_argument(
            '--now',
            dest='now_value',
            help='模拟当前时间，ISO 8601，如 2026-08-23T09:00:00',
        )
        parser.add_argument(
            '--customer-id',
            type=int,
            dest='customer_id',
            help='只处理指定客户的工单',
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

        stats = process_support_sla(
            now=now,
            dry_run=bool(options.get('dry_run')),
            apply_escalate=True if options.get('apply_escalate') else None,
            customer_id=options.get('customer_id'),
        )

        prefix = '[dry-run] ' if stats['dry_run'] else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}扫描 {stats["scanned"]} 张工单，'
            f'响应超时 {stats["response_breached"]}，'
            f'解决超时 {stats["resolve_breached"]}，'
            f'升级 {stats["escalated"]}，'
            f'跳过 {stats["skipped"]}'
        ))
        for line in stats.get('planned') or []:
            self.stdout.write(f'  - {line}')
