from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('system_manager', '0022_rename_backup_upgrade_indexes'),
    ]

    operations = [
        migrations.CreateModel(
            name='SystemOsTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=128, verbose_name='系统名称')),
                ('version', models.CharField(max_length=64, verbose_name='版本')),
                ('backup_filename', models.CharField(
                    help_text='PVE 存储中的文件名，如 vzdump-qemu-100-ubuntu-22.04.vma.zst',
                    max_length=255,
                    verbose_name='备份文件名',
                )),
                ('download_url', models.URLField(
                    help_text='POP 缺失该版本时按此 URL 下载，例如 http://mirror.example.com/ubuntu-22.04.vma.zst',
                    max_length=512,
                    verbose_name='下载地址',
                )),
                ('code', models.SlugField(blank=True, max_length=32, unique=True, verbose_name='模版代码')),
                ('os_type', models.CharField(
                    choices=[
                        ('debian', 'Debian'),
                        ('ubuntu', 'Ubuntu'),
                        ('centos', 'CentOS'),
                        ('rhel', 'RHEL'),
                        ('windows', 'Windows Server'),
                        ('other', '其他'),
                    ],
                    default='other',
                    max_length=16,
                    verbose_name='系统类型',
                )),
                ('source_type', models.CharField(
                    choices=[
                        ('backup', 'PVE 备份'),
                        ('iso', 'ISO'),
                        ('vztmpl', 'LXC 模板'),
                    ],
                    default='backup',
                    max_length=16,
                    verbose_name='文件类型',
                )),
                ('enabled', models.BooleanField(default=True, verbose_name='启用')),
                ('sort_order', models.PositiveSmallIntegerField(default=0, verbose_name='排序')),
                ('remark', models.CharField(blank=True, default='', max_length=255, verbose_name='备注')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '系统模版',
                'verbose_name_plural': '系统模版',
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.AddConstraint(
            model_name='systemostemplate',
            constraint=models.UniqueConstraint(
                fields=('name', 'version'),
                name='uniq_system_os_template_name_version',
            ),
        ),
    ]
