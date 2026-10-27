from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('product_manager', '0008_product_os_templates'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='is_hot_sale',
            field=models.BooleanField(
                default=False,
                help_text='默认不勾选。勾选后门户展示热卖；未勾选时，已采购满 2 个在服服务也会自动提示热卖',
                verbose_name='热卖',
            ),
        ),
    ]
