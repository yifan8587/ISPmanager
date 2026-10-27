from django.db import migrations


def forwards_add_profile_manage(apps, schema_editor):
    PermissionDefinition = apps.get_model('accounts', 'PermissionDefinition')
    RolePermissionGrant = apps.get_model('accounts', 'RolePermissionGrant')
    code = 'customer.detail.manage'
    if not PermissionDefinition.objects.filter(code=code, is_active=True).exists():
        return
    RolePermissionGrant.objects.get_or_create(role='customer', permission_id=code)


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_customer_role_billing_pay'),
    ]

    operations = [
        migrations.RunPython(forwards_add_profile_manage, backwards_noop),
    ]
