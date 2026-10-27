"""
PopPoint：资源类型 → 网络类型(network_type) + 新增 ASN/企业性质(asn_nature)
IPBlock ：资源类型 → IP 属性(ip_attribute)
手写迁移：通过 RenameField 保留既有数据，并对历史值做归一映射。
"""
from django.db import migrations, models


def normalize_forward(apps, schema_editor):
    PopPoint = apps.get_model('resource_manager', 'PopPoint')
    IPBlock = apps.get_model('resource_manager', 'IPBlock')

    # PopPoint：network_type 此刻仍保存旧 resource_type 的值
    for pop in PopPoint.objects.all():
        raw = (pop.network_type or '').strip()
        # ASN/企业性质：尽力从旧值推断，其余默认 ISP
        if raw == 'dual_isp':
            pop.asn_nature = 'dual_isp'
        elif raw == 'isp':
            pop.asn_nature = 'isp'
        else:
            pop.asn_nature = 'isp'
        # 网络类型：仅保留 家宽，其余统一归为 IDC
        pop.network_type = 'home_broadband' if raw == 'home_broadband' else 'idc'
        pop.save(update_fields=['network_type', 'asn_nature'])

    # IPBlock：ip_attribute 此刻仍保存旧 resource_type 的值
    for blk in IPBlock.objects.all():
        raw = (blk.ip_attribute or '').strip()
        if not raw:
            continue
        blk.ip_attribute = 'broadcast' if raw == 'broadcast' else 'native'
        blk.save(update_fields=['ip_attribute'])


def normalize_backward(apps, schema_editor):
    # 反向迁移无需数据处理（字段重命名已由 RenameField 处理）
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('resource_manager', '0018_bandwidthallocation_product_edgeacceleration_product_and_more'),
    ]

    operations = [
        # 先移除旧的 resource_type 索引
        migrations.RemoveIndex(
            model_name='poppoint',
            name='resource_ma_resourc_14f46e_idx',
        ),
        # PopPoint: resource_type → network_type（保留数据）
        migrations.RenameField(
            model_name='poppoint',
            old_name='resource_type',
            new_name='network_type',
        ),
        migrations.AlterField(
            model_name='poppoint',
            name='network_type',
            field=models.CharField(
                choices=[('home_broadband', '家宽'), ('idc', 'IDC')],
                default='idc', max_length=20, verbose_name='网络类型',
            ),
        ),
        migrations.AddField(
            model_name='poppoint',
            name='asn_nature',
            field=models.CharField(
                choices=[('isp', 'ISP'), ('non_isp', '非 ISP'), ('dual_isp', '双 ISP')],
                default='isp', help_text='ISP / 非ISP / 双ISP',
                max_length=16, verbose_name='ASN/企业性质',
            ),
        ),
        migrations.AddIndex(
            model_name='poppoint',
            index=models.Index(fields=['network_type'], name='resource_ma_network_idx'),
        ),
        migrations.AddIndex(
            model_name='poppoint',
            index=models.Index(fields=['asn_nature'], name='resource_ma_asn_idx'),
        ),
        # IPBlock: resource_type → ip_attribute（保留数据）
        migrations.RenameField(
            model_name='ipblock',
            old_name='resource_type',
            new_name='ip_attribute',
        ),
        migrations.AlterField(
            model_name='ipblock',
            name='ip_attribute',
            field=models.CharField(
                blank=True,
                choices=[('native', '原生'), ('broadcast', '广播')],
                help_text='原生 / 广播', max_length=20, verbose_name='IP 属性',
            ),
        ),
        migrations.RunPython(normalize_forward, normalize_backward),
    ]
