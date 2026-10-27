from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resource_manager', '0029_greconfig_remote_ip_nullable'),
    ]

    operations = [
        migrations.AddField(
            model_name='poppoint',
            name='ip_delivery_mode',
            field=models.CharField(
                choices=[('public', '公网直连'), ('nat', 'NAT 交付')],
                db_index=True,
                default='public',
                help_text='公网直连：业务 IP 可直接路由到客户；NAT 交付：仅能经节点 NAT 访问，产品与门户需提示',
                max_length=16,
                verbose_name='IP 交付方式',
            ),
        ),
        migrations.AddField(
            model_name='ipblock',
            name='gateway',
            field=models.GenericIPAddressField(
                blank=True,
                help_text='该 CIDR 默认网关，供后续 VPS 网卡配置使用；单 IP 可覆盖',
                null=True,
                verbose_name='本段网关',
            ),
        ),
        migrations.AddField(
            model_name='ipaddress',
            name='gateway',
            field=models.GenericIPAddressField(
                blank=True,
                help_text='留空则使用所属地址块的本段网关',
                null=True,
                verbose_name='网关',
            ),
        ),
    ]
