from django.core.management.base import BaseCommand

from service_manager.instance_backfill import backfill_orphan_service_instances


class Command(BaseCommand):
    help = '为未关联 ServiceInstance 的客户 VPS / 边缘加速批量回填服务实例（不在门户列表接口写库）'

    def add_arguments(self, parser):
        parser.add_argument('--customer-id', type=int, default=None, help='仅处理指定客户')
        parser.add_argument('--dry-run', action='store_true', help='只统计，不写库')

    def handle(self, *args, **options):
        stats = backfill_orphan_service_instances(
            customer_id=options.get('customer_id'),
            dry_run=bool(options.get('dry_run')),
        )
        prefix = '[dry-run] ' if options.get('dry_run') else ''
        self.stdout.write(
            f"{prefix}孤儿 VPS {stats['vps_found']}，回填 {stats['vps_created']}；"
            f"孤儿 Edge {stats['edge_found']}，回填 {stats['edge_created']}"
        )
        for err in stats['errors']:
            self.stderr.write(self.style.ERROR(err))
        if not stats['errors']:
            self.stdout.write(self.style.SUCCESS(f'{prefix}完成'))
