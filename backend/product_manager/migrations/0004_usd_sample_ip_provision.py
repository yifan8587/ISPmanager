from django.db import migrations, models


def migrate_product_currency_to_usd(apps, schema_editor):
    Product = apps.get_model('product_manager', 'Product')
    TemplatePricing = apps.get_model('product_manager', 'TemplatePricing')
    Product.objects.exclude(currency='USD').update(currency='USD')
    TemplatePricing.objects.exclude(currency='USD').update(currency='USD')
    SystemSetting = apps.get_model('system_manager', 'SystemSetting')
    SystemSetting.objects.filter(key='default_currency').update(
        value='USD', default_value='USD',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('product_manager', '0003_product_billing_mode'),
        ('system_manager', '0022_rename_backup_upgrade_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='currency',
            field=models.CharField(default='USD', max_length=8, verbose_name='币种'),
        ),
        migrations.AlterField(
            model_name='templatepricing',
            name='currency',
            field=models.CharField(default='USD', max_length=8, verbose_name='币种'),
        ),
        migrations.AddField(
            model_name='product',
            name='sample_ip',
            field=models.CharField(
                blank=True, default='',
                help_text='门户展示用的示例地址，可填单个或多个（逗号分隔）',
                max_length=255, verbose_name='示例 IP',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='sample_ip_visible',
            field=models.BooleanField(
                default=False,
                help_text='勾选后在客户门户产品页展示示例 IP',
                verbose_name='显示示例 IP',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='estimated_provision_hours',
            field=models.PositiveIntegerField(
                blank=True, help_text='库存不足仍上线时填写；客户付款后据此提示预计交付时间',
                null=True, verbose_name='预估开通时间(小时)',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='allow_backorder',
            field=models.BooleanField(
                default=False,
                help_text='库存不足时仍允许上线和下单，按预估开通时间交付',
                verbose_name='允许缺货售卖',
            ),
        ),
        migrations.RunPython(migrate_product_currency_to_usd, migrations.RunPython.noop),
    ]
