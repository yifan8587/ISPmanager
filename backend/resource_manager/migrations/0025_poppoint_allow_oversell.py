from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resource_manager', '0024_edge_tunnel_mtu'),
    ]

    operations = [
        migrations.AddField(
            model_name='poppoint',
            name='allow_oversell',
            field=models.BooleanField(
                default=False,
                help_text='关闭时下单预占必须落在剩余有效容量内；打开后仍记账本，但允许预占超过剩余容量',
                verbose_name='允许超售',
            ),
        ),
    ]
