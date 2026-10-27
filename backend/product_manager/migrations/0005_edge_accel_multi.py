from django.db import migrations, models


def migrate_accel_types(apps, schema_editor):
    mapping = {
        'proxy': 'gre,wireguard',
        'socks5': 'gre,wireguard',
        'socks': 'gre,wireguard',
    }
    for model_name in ('ProductSpec', 'ProductTemplate', 'Product'):
        model = apps.get_model('product_manager', model_name)
        for row in model.objects.all().only('id', 'accel_type'):
            raw = (row.accel_type or '').strip().lower()
            if raw in mapping:
                row.accel_type = mapping[raw]
                row.save(update_fields=['accel_type'])


class Migration(migrations.Migration):

    dependencies = [
        ('product_manager', '0004_usd_sample_ip_provision'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='accel_type',
            field=models.CharField(
                blank=True,
                help_text='仅边缘加速有效，可多选 GRE/WireGuard，逗号分隔。客户购买后自行选择对接模式。',
                max_length=64,
                verbose_name='加速类型',
            ),
        ),
        migrations.AlterField(
            model_name='productspec',
            name='accel_type',
            field=models.CharField(
                blank=True,
                help_text='仅边缘加速有效，可多选 GRE/WireGuard，逗号分隔。客户购买后自行选择对接模式。',
                max_length=64,
                verbose_name='加速类型',
            ),
        ),
        migrations.AlterField(
            model_name='producttemplate',
            name='accel_type',
            field=models.CharField(
                blank=True,
                help_text='仅边缘加速有效，可多选 GRE/WireGuard，逗号分隔。客户购买后自行选择对接模式。',
                max_length=64,
                verbose_name='加速类型',
            ),
        ),
        migrations.RunPython(migrate_accel_types, migrations.RunPython.noop),
    ]
