from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resource_manager', '0014_networkinterface_netplan_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='ipaddress',
            name='interface_code',
            field=models.CharField(
                blank=True,
                default='',
                help_text='节点 resourcemanage interface_code，与 interfacemanage 约定一致',
                max_length=128,
                verbose_name='接口标识',
            ),
        ),
        migrations.AddField(
            model_name='ipaddress',
            name='subnet_label',
            field=models.CharField(
                blank=True,
                default='',
                help_text='网段/分组标签，与节点 subnet_label 对齐',
                max_length=128,
                verbose_name='网段标签',
            ),
        ),
        migrations.AddField(
            model_name='ipaddress',
            name='node_route_ref',
            field=models.CharField(
                blank=True,
                default='',
                help_text='节点 desired-route UUID（边缘 IP 联动路由）',
                max_length=64,
                verbose_name='节点路由引用',
            ),
        ),
    ]
