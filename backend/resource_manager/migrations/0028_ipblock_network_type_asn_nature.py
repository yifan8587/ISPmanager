"""IP 地址块增加网络类型 / ASN，并建立可动态维护的网络类型目录。"""
from django.db import migrations, models


def seed_and_backfill(apps, schema_editor):
    NetworkTypeOption = apps.get_model('resource_manager', 'NetworkTypeOption')
    IPBlock = apps.get_model('resource_manager', 'IPBlock')
    PopPoint = apps.get_model('resource_manager', 'PopPoint')

    NetworkTypeOption.objects.get_or_create(
        code='idc', defaults={'name': 'IDC', 'sort_order': 0, 'is_active': True},
    )
    NetworkTypeOption.objects.get_or_create(
        code='home_broadband', defaults={'name': '家宽', 'sort_order': 1, 'is_active': True},
    )

    for pop in PopPoint.objects.all().only('id', 'network_type', 'asn_nature'):
        IPBlock.objects.filter(pop_point_id=pop.id).update(
            network_type=pop.network_type or 'idc',
            asn_nature=pop.asn_nature or 'isp',
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('resource_manager', '0027_availability_zone'),
    ]

    operations = [
        migrations.CreateModel(
            name='NetworkTypeOption',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.SlugField(help_text='如 idc、home_broadband', max_length=32, unique=True, verbose_name='代码')),
                ('name', models.CharField(max_length=64, verbose_name='名称')),
                ('sort_order', models.IntegerField(default=0, verbose_name='排序')),
                ('is_active', models.BooleanField(default=True, verbose_name='启用')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '网络类型',
                'verbose_name_plural': '网络类型列表',
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.AddField(
            model_name='ipblock',
            name='network_type',
            field=models.CharField(
                db_index=True,
                default='idc',
                help_text='对应网络类型目录代码，如 idc / home_broadband',
                max_length=32,
                verbose_name='网络类型',
            ),
        ),
        migrations.AddField(
            model_name='ipblock',
            name='asn_nature',
            field=models.CharField(
                choices=[('isp', '单ISP'), ('dual_isp', '双ISP'), ('non_isp', '非ISP')],
                db_index=True,
                default='isp',
                help_text='单ISP / 双ISP / 非ISP，默认单ISP',
                max_length=16,
                verbose_name='ASN/企业性质',
            ),
        ),
        migrations.AlterField(
            model_name='poppoint',
            name='asn_nature',
            field=models.CharField(
                choices=[('isp', '单ISP'), ('dual_isp', '双ISP'), ('non_isp', '非ISP')],
                default='isp',
                help_text='单ISP / 双ISP / 非ISP（产品展示已改为读取 IP 地址块）',
                max_length=16,
                verbose_name='ASN/企业性质',
            ),
        ),
        migrations.RunPython(seed_and_backfill, noop_reverse),
    ]
