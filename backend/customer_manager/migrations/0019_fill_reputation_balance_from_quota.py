"""开通了额度但余额仍为 0、且从未信誉值支付过的客户，按额度入账。"""
from django.db import migrations
from django.db.models import F


def fill_unused_quota(apps, schema_editor):
    Customer = apps.get_model('customer_manager', 'Customer')
    CustomerBilling = apps.get_model('customer_manager', 'CustomerBilling')
    paid_ids = set(
        CustomerBilling.objects.filter(payment_method='reputation')
        .values_list('customer_id', flat=True)
    )
    qs = (
        Customer.objects.exclude(reputation_mode='none')
        .filter(reputation_balance=0)
        .exclude(reputation_quota=0)
        .exclude(id__in=paid_ids)
    )
    qs.update(reputation_balance=F('reputation_quota'))


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('customer_manager', '0018_customer_reputation'),
    ]

    operations = [
        migrations.RunPython(fill_unused_quota, noop_reverse),
    ]
