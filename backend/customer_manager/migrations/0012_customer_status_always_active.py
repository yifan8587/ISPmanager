from django.db import migrations, models


def activate_all_customers(apps, schema_editor):
    Customer = apps.get_model('customer_manager', 'Customer')
    Customer.objects.exclude(status='active').update(status='active')


class Migration(migrations.Migration):

    dependencies = [
        ('customer_manager', '0011_customerbilling_gross_amount_and_more'),
    ]

    operations = [
        migrations.RunPython(activate_all_customers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='customer',
            name='status',
            field=models.CharField(
                choices=[('active', '正常')],
                default='active',
                max_length=16,
                verbose_name='状态',
            ),
        ),
    ]
