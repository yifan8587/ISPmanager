from django.db import migrations


def forwards_align_channel_grants(apps, schema_editor):
    from accounts.permission_service import align_channel_role_grants, sync_permission_catalog
    sync_permission_catalog()
    align_channel_role_grants()


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_channel_role_and_profile'),
    ]

    operations = [
        migrations.RunPython(forwards_align_channel_grants, backwards_noop),
    ]
