from django.db import migrations


def forwards(apps, schema_editor):
    SystemSetting = apps.get_model('system_manager', 'SystemSetting')
    grace = SystemSetting.objects.filter(key='instance_grace_days').first()
    if grace and str(grace.value).strip() in {'', '3'}:
        grace.value = '1'
        grace.save(update_fields=['value'])
    recycle = SystemSetting.objects.filter(key='instance_auto_recycle_after_grace').first()
    if recycle and str(recycle.value).strip().lower() in {'', '0', 'false', 'no', 'off'}:
        recycle.value = 'true'
        recycle.save(update_fields=['value'])
    reminder = SystemSetting.objects.filter(key='instance_reminder_days').first()
    if reminder and str(reminder.value).strip() in {'', '7,3,1'}:
        reminder.value = '3'
        reminder.save(update_fields=['value'])


class Migration(migrations.Migration):

    dependencies = [
        ('system_manager', '0025_os_template_cloud_source'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
