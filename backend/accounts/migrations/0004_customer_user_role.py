# Generated manually for customer portal default role

from django.db import migrations, models


def forwards_seed_customer_role(apps, schema_editor):
    UserProfile = apps.get_model('accounts', 'UserProfile')
    PermissionDefinition = apps.get_model('accounts', 'PermissionDefinition')
    RolePermissionGrant = apps.get_model('accounts', 'RolePermissionGrant')

    # 已绑定客户但角色非法的账号，统一改为门户默认角色 customer
    allowed = {'customer', 'readonly'}
    for profile in UserProfile.objects.filter(account_type='customer').exclude(role__in=allowed):
        profile.role = 'customer'
        profile.save(update_fields=['role'])

    # 若权限目录已初始化，为 customer 角色写入默认授权（与 ROLE_PERMISSIONS['customer'] 对齐）
    if not PermissionDefinition.objects.exists():
        return

    default_codes = [
        'customer.view',
        'customer.detail.view',
        'billing.view',
        'billing.list.view',
        'service.view',
        'service.manage',
        'service.overview.view',
        'service.order.view',
        'service.order.manage',
        'service.instance.view',
        'service.instance.manage',
        'service.change.view',
        'service.change.manage',
        'resource.view',
        'resource.vps.view',
        'resource.vps.manage',
        'resource.edge.view',
        'resource.edge.manage',
    ]
    if RolePermissionGrant.objects.filter(role='customer').exists():
        return

    active = set(
        PermissionDefinition.objects.filter(
            code__in=default_codes, is_active=True,
        ).values_list('code', flat=True)
    )
    RolePermissionGrant.objects.bulk_create([
        RolePermissionGrant(role='customer', permission_id=code)
        for code in active
    ], ignore_conflicts=True)


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_permission_catalog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='rolepermissiongrant',
            name='role',
            field=models.CharField(
                choices=[
                    ('superadmin', '超级管理员'),
                    ('admin', '管理员'),
                    ('customer_manager', '客户管理员'),
                    ('operator', '运维人员'),
                    ('sales', '销售人员'),
                    ('customer', '客户用户'),
                    ('readonly', '只读用户'),
                ],
                db_index=True,
                max_length=20,
                verbose_name='角色',
            ),
        ),
        migrations.AlterField(
            model_name='userprofile',
            name='role',
            field=models.CharField(
                choices=[
                    ('superadmin', '超级管理员'),
                    ('admin', '管理员'),
                    ('customer_manager', '客户管理员'),
                    ('operator', '运维人员'),
                    ('sales', '销售人员'),
                    ('customer', '客户用户'),
                    ('readonly', '只读用户'),
                ],
                default='operator',
                max_length=20,
                verbose_name='系统角色',
            ),
        ),
        migrations.RunPython(forwards_seed_customer_role, backwards_noop),
    ]
