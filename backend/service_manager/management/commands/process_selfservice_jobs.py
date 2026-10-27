from django.core.management.base import BaseCommand

from service_manager.self_service import ensure_default_os_images, process_selfservice_jobs


class Command(BaseCommand):
    help = (
        '处理门户重装/快照自助任务：默认只报告待处理任务；'
        '--apply 时按骨架完成（改 OS 档案/标记快照可用），不调用节点'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='只报告将要处理的任务，不写库',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='将待处理任务按骨架标记完成（不调用 PVE）',
        )
        parser.add_argument(
            '--customer-id',
            type=int,
            dest='customer_id',
            help='只处理指定客户的任务',
        )
        parser.add_argument(
            '--seed-images',
            action='store_true',
            help='若不存在则写入预置镜像目录',
        )

    def handle(self, *args, **options):
        if options.get('seed_images'):
            created = ensure_default_os_images()
            self.stdout.write(f'预置镜像新增 {created} 条')

        stats = process_selfservice_jobs(
            dry_run=bool(options.get('dry_run')),
            apply=bool(options.get('apply')),
            customer_id=options.get('customer_id'),
        )
        prefix = '[dry-run] ' if stats['dry_run'] else ''
        mode = '已应用' if stats['apply'] and not stats['dry_run'] else '仅报告'
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}{mode}：扫描 {stats["scanned"]}，'
            f'完成 {stats["completed"]}，跳过 {stats["skipped"]}'
        ))
        for line in stats.get('planned') or []:
            self.stdout.write(f'  - {line}')
