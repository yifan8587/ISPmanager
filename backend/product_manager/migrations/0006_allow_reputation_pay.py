from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('product_manager', '0005_edge_accel_multi'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='allow_reputation_pay',
            field=models.BooleanField(
                default=False,
                help_text='勾选后，已开通信誉值的客户可用信誉值支付本产品；否则只能走其他支付方式',
                verbose_name='允许信誉值支付',
            ),
        ),
    ]
