from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('system_manager', '0021_system_backup_upgrade'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='systembackup',
            new_name='system_mana_status_56282e_idx',
            old_name='system_mana_status_7f1b0a_idx',
        ),
        migrations.RenameIndex(
            model_name='systembackup',
            new_name='system_mana_backup__785d8d_idx',
            old_name='system_mana_backup__3c2e1d_idx',
        ),
        migrations.RenameIndex(
            model_name='systemupgradepackage',
            new_name='system_mana_status_84bd4c_idx',
            old_name='system_mana_status_9a4c2e_idx',
        ),
    ]
