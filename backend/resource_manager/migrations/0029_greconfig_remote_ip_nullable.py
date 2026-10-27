from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resource_manager', '0028_ipblock_network_type_asn_nature'),
    ]

    operations = [
        migrations.AlterField(
            model_name='greconfig',
            name='remote_ip',
            field=models.GenericIPAddressField(
                blank=True,
                null=True,
                verbose_name='远端 IP',
                help_text='客户侧公网 IP；开通时预分配可留空，由客户在门户补全',
            ),
        ),
    ]
