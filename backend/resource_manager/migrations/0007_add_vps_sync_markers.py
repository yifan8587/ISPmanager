from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resource_manager', '0006_add_sync_markers'),
    ]

    operations = [
        migrations.AddField(
            model_name='vps',
            name='is_synced',
            field=models.BooleanField(blank=True, default=False, help_text='由节点 PVE 虚机同步生成，可被同步对账自动清理', null=True, verbose_name='节点同步导入'),
        ),
        migrations.AddField(
            model_name='vps',
            name='source_node',
            field=models.CharField(blank=True, max_length=128, verbose_name='来源 PVE 节点'),
        ),
        migrations.AddField(
            model_name='vps',
            name='source_vmid',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='来源 VMID'),
        ),
        migrations.AddField(
            model_name='vps',
            name='last_synced_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最近同步时间'),
        ),
    ]
