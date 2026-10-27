from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('service_manager', '0017_self_service_reinstall_snapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='serviceorder',
            name='expected_delivery_at',
            field=models.DateTimeField(
                blank=True,
                help_text='下单时根据产品预估开通时间或节点交付能力计算',
                null=True,
                verbose_name='预计交付时间',
            ),
        ),
    ]
