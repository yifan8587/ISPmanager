from django.db import migrations


def seed_centos_cloud_images(apps, schema_editor):
    """只补 CentOS 7/8。全量目录会把 Ubuntu/Debian 写进测试库，和用例冲突。"""
    from system_manager.os_templates import DEFAULT_CLOUD_CATALOG, apply_catalog_items

    centos = [item for item in DEFAULT_CLOUD_CATALOG if str(item.get('code') or '').startswith('centos-')]
    apply_catalog_items(centos)


class Migration(migrations.Migration):

    dependencies = [
        ('system_manager', '0026_expiry_policy_defaults'),
    ]

    operations = [
        migrations.RunPython(seed_centos_cloud_images, migrations.RunPython.noop),
    ]
