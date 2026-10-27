from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('product_manager', '0007_rename_product_man_billing_7e2c1a_idx_product_man_billing_b8e1bd_idx_and_more'),
        ('system_manager', '0023_system_os_template'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='os_templates',
            field=models.ManyToManyField(
                blank=True,
                help_text='VPS 产品可选的系统模版；不选表示门户展示全部已启用模版',
                related_name='products',
                to='system_manager.systemostemplate',
                verbose_name='可选系统版本',
            ),
        ),
    ]
