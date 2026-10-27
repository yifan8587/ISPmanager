from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('service_manager', '0020_align_service_order_fk_on_delete'),
        ('system_manager', '0023_system_os_template'),
    ]

    operations = [
        migrations.AddField(
            model_name='serviceorder',
            name='requested_os_template',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='service_orders',
                to='system_manager.systemostemplate',
                verbose_name='期望系统模版',
            ),
        ),
        migrations.AlterField(
            model_name='osimage',
            name='node_template_ref',
            field=models.CharField(
                blank=True,
                help_text='对应中心系统模版的备份文件名，开通时下发到 POP / PVE',
                max_length=255,
                verbose_name='节点模板引用',
            ),
        ),
        migrations.AddField(
            model_name='osimage',
            name='os_template',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='os_image',
                to='system_manager.systemostemplate',
                verbose_name='系统模版',
            ),
        ),
    ]
