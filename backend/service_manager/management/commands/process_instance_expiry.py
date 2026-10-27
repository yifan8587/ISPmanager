from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from service_manager.expiry_engine import process_instance_expiry


class Command(BaseCommand):
    help = (
        '扫描服务实例到期日程：T-N 催缴、到期通知；'
        '仅在系统参数打开或指定 --apply-suspend / --apply-recycle 时停机或回收'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='只报告将要执行的动作，不写库、不通知、不停机/回收',
        )
        parser.add_argument(
            '--apply-suspend',
            action='store_true',
            help='本次运行允许到期停机（覆盖系统参数关闭状态）',
        )
        parser.add_argument(
            '--apply-recycle',
            action='store_true',
            help='本次运行允许宽限后回收（覆盖系统参数关闭状态）',
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
            help='只处理指定客户的实例',
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

        stats = process_instance_expiry(
            now=now,
            dry_run=bool(options.get('dry_run')),
            apply_suspend=True if options.get('apply_suspend') else None,
            apply_recycle=True if options.get('apply_recycle') else None,
            customer_id=options.get('customer_id'),
        )

        prefix = '[dry-run] ' if stats['dry_run'] else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}扫描 {stats["scanned"]} 个实例，'
            f'回填到期 {stats["expire_backfilled"]}，'
            f'回填日程 {stats["schedule_updated"]}，'
            f'催缴 {stats["reminded"]}，'
            f'到期通知 {stats["expired"]}，'
            f'停机 {stats["suspended"]}，'
            f'回收 {stats["recycled"]}，'
            f'跳过 {stats["skipped"]}，'
            f'错误 {stats["errors"]}'
        ))
        for line in stats.get('planned') or []:
            self.stdout.write(f'  - {line}')
