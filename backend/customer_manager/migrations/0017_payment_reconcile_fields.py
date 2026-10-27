from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customer_manager', '0016_wallet_recharge_and_ledger'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymenttransaction',
            name='last_reconciled_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最后对账时间'),
        ),
        migrations.AddField(
            model_name='paymenttransaction',
            name='last_reconcile_status',
            field=models.CharField(blank=True, max_length=24, verbose_name='最后对账结果'),
        ),
        migrations.AddField(
            model_name='paymenttransaction',
            name='last_reconcile_error',
            field=models.CharField(blank=True, max_length=256, verbose_name='最后对账错误'),
        ),
    ]
