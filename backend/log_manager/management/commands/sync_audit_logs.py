"""
回填历史分模块日志到统一审计表，并同步「操作日志」页面权限。

    python manage.py sync_audit_logs
    python manage.py sync_audit_logs --skip-permissions
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = '回填历史操作日志到统一审计表，并同步操作日志页权限'

    def add_arguments(self, parser):
        parser.add_argument('--skip-permissions', action='store_true', help='不同步权限目录')
        parser.add_argument('--limit', type=int, default=None, help='每个来源最多回填条数')

    def handle(self, *args, **options):
        from log_manager.ingest import ingest_existing_logs

        stats = ingest_existing_logs(limit=options.get('limit'))
        self.stdout.write(self.style.SUCCESS(f'历史日志回填完成: {stats}'))

        if not options.get('skip_permissions'):
            from accounts.permission_service import sync_builtin_modules
            result = sync_builtin_modules(['system'])
            self.stdout.write(self.style.SUCCESS(f'权限同步完成: {result}'))
