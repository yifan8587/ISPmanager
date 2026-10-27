from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('system_manager', '0024_smtp_settings'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemostemplate',
            name='backup_filename',
            field=models.CharField(
                help_text='PVE 存储中的 Cloud 镜像文件名，如 noble-server-cloudimg-amd64.img',
                max_length=255,
                verbose_name='镜像文件名',
            ),
        ),
        migrations.AlterField(
            model_name='systemostemplate',
            name='download_url',
            field=models.URLField(
                help_text='POP 缺失该版本时按此 URL 下载，例如 https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img',
                max_length=512,
                verbose_name='下载地址',
            ),
        ),
        migrations.AlterField(
            model_name='systemostemplate',
            name='source_type',
            field=models.CharField(
                choices=[
                    ('cloud', 'Cloud 镜像'),
                    ('backup', 'PVE 备份'),
                    ('iso', 'ISO 安装盘'),
                    ('vztmpl', 'LXC 模板'),
                ],
                default='cloud',
                max_length=16,
                verbose_name='文件类型',
            ),
        ),
    ]
