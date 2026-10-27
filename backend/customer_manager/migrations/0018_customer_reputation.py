from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customer_manager', '0017_payment_reconcile_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='reputation_mode',
            field=models.CharField(
                choices=[('none', '关闭'), ('one_time', '一次性'), ('monthly', '按月更新')],
                default='none',
                help_text='关闭则不可信誉值支付；一次性用完为止；按月更新每月初重置为额度',
                max_length=16,
                verbose_name='信誉值模式',
            ),
        ),
        migrations.AddField(
            model_name='customer',
            name='reputation_quota',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=12, verbose_name='信誉值额度',
            ),
        ),
        migrations.AddField(
            model_name='customer',
            name='reputation_balance',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=12, verbose_name='信誉值余额',
            ),
        ),
        migrations.AddField(
            model_name='customer',
            name='reputation_period',
            field=models.CharField(
                blank=True,
                default='',
                help_text='按月模式记录最近重置的 YYYY-MM',
                max_length=7,
                verbose_name='信誉值账期',
            ),
        ),
        migrations.AddField(
            model_name='channelprofile',
            name='reputation_grant_mode',
            field=models.CharField(
                choices=[('none', '关闭'), ('one_time', '一次性'), ('monthly', '按月更新')],
                default='none',
                help_text='通过该优惠码注册的客户获得的信誉值模式',
                max_length=16,
                verbose_name='注册授信模式',
            ),
        ),
        migrations.AddField(
            model_name='channelprofile',
            name='reputation_grant_amount',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='例如 100 表示注册后获得 100 信誉值，可用于一次或按月信誉支付',
                max_digits=12,
                verbose_name='注册授信额度',
            ),
        ),
        migrations.AlterField(
            model_name='customerbilling',
            name='payment_method',
            field=models.CharField(
                blank=True,
                choices=[
                    ('bank_transfer', '银行转账'),
                    ('alipay', '支付宝'),
                    ('wechat', '微信支付'),
                    ('paypal', 'PayPal'),
                    ('stripe', 'Stripe'),
                    ('airwallex', 'Airwallex'),
                    ('unionpay', '银联在线支付'),
                    ('cash', '现金'),
                    ('credit_payment', '信用支付'),
                    ('reputation', '信誉值支付'),
                    ('balance', '账户余额'),
                    ('other', '其他'),
                ],
                max_length=16,
                verbose_name='支付方式',
            ),
        ),
    ]
