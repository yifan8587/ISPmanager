from django.db import migrations


def create_default_profiles(apps, schema_editor):
    MonitoringGraphProfile = apps.get_model('operation_manager', 'MonitoringGraphProfile')
    defaults = [
        {
            'code': 'traffic_timeseries_default',
            'name': '接口流量时序图',
            'profile_type': 'timeseries',
            'aggregation': 'avg',
            'percentile_n': 95,
            'billing_mode': 'max_in_out',
            'unit_display': 'Mbps',
            'sample_align_sec': 300,
            'item_key_in_patterns': ['net.if.in*', '*ifHCInOctets*', '*ifInOctets*'],
            'item_key_out_patterns': ['net.if.out*', '*ifHCOutOctets*', '*ifOutOctets*'],
            'stack_series': True,
            'fill_missing': 'skip',
            'convert_counter_to_rate': True,
            'is_system': True,
            'is_active': True,
            'remark': '默认接口入/出流量时序展示',
        },
        {
            'code': 'traffic_p95_default',
            'name': '95峰值计费（max(入,出)）',
            'profile_type': 'percentile_billing',
            'aggregation': 'p95',
            'percentile_n': 95,
            'billing_mode': 'max_in_out',
            'unit_display': 'Mbps',
            'sample_align_sec': 300,
            'item_key_in_patterns': ['net.if.in*', '*ifHCInOctets*'],
            'item_key_out_patterns': ['net.if.out*', '*ifHCOutOctets*'],
            'stack_series': False,
            'fill_missing': 'skip',
            'convert_counter_to_rate': True,
            'is_system': True,
            'is_active': True,
            'remark': 'ISP 常用 95 计费：每 5 分钟取 max(入,出) 再算 95 分位',
        },
        {
            'code': 'traffic_p99_default',
            'name': '99峰值计费',
            'profile_type': 'percentile_billing',
            'aggregation': 'p99',
            'percentile_n': 99,
            'billing_mode': 'max_in_out',
            'unit_display': 'Mbps',
            'sample_align_sec': 300,
            'item_key_in_patterns': ['net.if.in*', '*ifHCInOctets*'],
            'item_key_out_patterns': ['net.if.out*', '*ifHCOutOctets*'],
            'stack_series': False,
            'fill_missing': 'skip',
            'convert_counter_to_rate': True,
            'is_system': True,
            'is_active': True,
        },
    ]
    for row in defaults:
        MonitoringGraphProfile.objects.update_or_create(code=row['code'], defaults=row)


def remove_default_profiles(apps, schema_editor):
    MonitoringGraphProfile = apps.get_model('operation_manager', 'MonitoringGraphProfile')
    MonitoringGraphProfile.objects.filter(
        code__in=['traffic_timeseries_default', 'traffic_p95_default', 'traffic_p99_default'],
        is_system=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('operation_manager', '0002_monitoring_custom_graphs'),
    ]

    operations = [
        migrations.RunPython(create_default_profiles, remove_default_profiles),
    ]
