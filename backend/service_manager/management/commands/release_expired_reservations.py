from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from service_manager.capacity_reservation import process_expired_reservations


class Command(BaseCommand):
    help = '释放超时未付的 POP 容量预占；已付款或履约中的工单不会释放'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='只报告将要释放的预占，不写库',
        )
        parser.add_argument(
            '--now',
            dest='now_value',
            help='模拟当前时间，ISO 8601，如 2026-08-23T09:00:00',
        )
        parser.add_argument(
            '--pop-id',
            type=int,
            dest='pop_id',
            help='只处理指定 POP',
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

        stats = process_expired_reservations(
            now=now,
            dry_run=bool(options.get('dry_run')),
            pop_id=options.get('pop_id'),
        )
        prefix = '[dry-run] ' if stats['dry_run'] else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}扫描 {stats["scanned"]} 条预占，'
            f'释放 {stats["released"]}，'
            f'跳过 {stats["skipped"]}'
        ))
        for line in stats.get('planned') or []:
            self.stdout.write(f'  - {line}')
