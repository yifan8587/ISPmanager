from django.db import migrations


class Migration(migrations.Migration):
    """触发 post_migrate，写入邮件服务器等新增系统参数。"""

    dependencies = [
        ('system_manager', '0023_system_os_template'),
    ]

    operations = []
