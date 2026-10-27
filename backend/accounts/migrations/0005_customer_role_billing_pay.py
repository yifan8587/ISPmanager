from django.db import migrations


def forwards_add_billing_pay(apps, schema_editor):
    PermissionDefinition = apps.get_model('accounts', 'PermissionDefinition')
    RolePermissionGrant = apps.get_model('accounts', 'RolePermissionGrant')
    code = 'billing.list.manage'
    if not PermissionDefinition.objects.filter(code=code, is_active=True).exists():
        return
    RolePermissionGrant.objects.get_or_create(role='customer', permission_id=code)


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_customer_user_role'),
    ]

    operations = [
        migrations.RunPython(forwards_add_billing_pay, backwards_noop),
    ]
