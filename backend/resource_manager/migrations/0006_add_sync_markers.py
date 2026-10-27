from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resource_manager', '0005_add_pop_deploy_fields'),
    ]

    operations = [
        # NetworkInterface
        migrations.AddField(
            model_name='networkinterface',
            name='is_synced',
            field=models.BooleanField(blank=True, default=False, help_text='由节点 pemanager 同步生成，可被同步对账自动清理', null=True, verbose_name='节点同步导入'),
        ),
        migrations.AddField(
            model_name='networkinterface',
            name='last_synced_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最近同步时间'),
        ),
        # RouteTable
        migrations.AddField(
            model_name='routetable',
            name='is_synced',
            field=models.BooleanField(blank=True, default=False, null=True, verbose_name='节点同步导入'),
        ),
        migrations.AddField(
            model_name='routetable',
            name='last_synced_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最近同步时间'),
        ),
        # StaticRoute
        migrations.AddField(
            model_name='staticroute',
            name='is_synced',
            field=models.BooleanField(blank=True, default=False, null=True, verbose_name='节点同步导入'),
        ),
        migrations.AddField(
            model_name='staticroute',
            name='last_synced_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最近同步时间'),
        ),
        # PolicyRoute
        migrations.AddField(
            model_name='policyroute',
            name='is_synced',
            field=models.BooleanField(blank=True, default=False, null=True, verbose_name='节点同步导入'),
        ),
        migrations.AddField(
            model_name='policyroute',
            name='last_synced_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最近同步时间'),
        ),
        # IPBlock
        migrations.AddField(
            model_name='ipblock',
            name='is_synced',
            field=models.BooleanField(blank=True, default=False, null=True, verbose_name='节点同步导入'),
        ),
        migrations.AddField(
            model_name='ipblock',
            name='last_synced_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最近同步时间'),
        ),
        # IPAddress
        migrations.AddField(
            model_name='ipaddress',
            name='is_synced',
            field=models.BooleanField(blank=True, default=False, null=True, verbose_name='节点同步导入'),
        ),
        migrations.AddField(
            model_name='ipaddress',
            name='last_synced_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最近同步时间'),
        ),
    ]
