"""
增量同步内置模块权限目录（add-only）。
新增业务模块（产品管理、供应商管理等）后运行：
    python manage.py sync_permissions
可选指定模块：
    python manage.py sync_permissions --modules product supplier
"""
from django.core.management.base import BaseCommand

from accounts.permission_service import sync_builtin_modules


class Command(BaseCommand):
    help = '增量同步内置模块的权限目录与角色授权（不删除已有自定义数据）'

    def add_arguments(self, parser):
        parser.add_argument('--modules', nargs='*', default=None,
                            help='仅同步指定模块 key，如 product supplier')

    def handle(self, *args, **options):
        result = sync_builtin_modules(options.get('modules'))
        self.stdout.write(self.style.SUCCESS(f'权限同步完成: {result}'))
