from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('product_manager', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='billing_mode',
            field=models.CharField(
                choices=[
                    ('flat', '包月'),
                    ('95th', '95 计费'),
                    ('avg', '平均带宽'),
                    ('transfer_quota', '流量包'),
                ],
                default='flat',
                help_text='包月只收月租；95/平均按承诺带宽出基础月租，超额单价留给流量出账；流量包按包含流量计',
                max_length=16,
                verbose_name='计费模式',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='overage_unit_price',
            field=models.DecimalField(
                decimal_places=4,
                default=0,
                help_text='流量包为每 GB；95/平均为超出承诺带宽后的每 Mbps。本步只存字段，不出超额账单',
                max_digits=12,
                verbose_name='超额单价',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='transfer_quota_gb',
            field=models.PositiveIntegerField(
                blank=True,
                help_text='仅流量包模式使用；每月包含的传输量',
                null=True,
                verbose_name='流量包额度(GB/月)',
            ),
        ),
        migrations.AddIndex(
            model_name='product',
            index=models.Index(fields=['billing_mode'], name='product_man_billing_7e2c1a_idx'),
        ),
    ]
