import django.db.models.deletion
from django.db import migrations, models


def forwards_sync_permissions(apps, schema_editor):
    from accounts.permission_service import sync_permission_catalog
    sync_permission_catalog()


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_customer_role_profile_manage'),
        ('supplier_manager', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='supplier',
            field=models.ForeignKey(
                blank=True,
                help_text='角色为「供应商」时必填，账号仅能维护该供应商的产品与资源',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='portal_accounts',
                to='supplier_manager.supplier',
                verbose_name='绑定供应商',
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
                    ('supplier', '供应商'),
                    ('readonly', '只读用户'),
                ],
                default='operator',
                max_length=20,
                verbose_name='系统角色',
            ),
        ),
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
                    ('supplier', '供应商'),
                    ('readonly', '只读用户'),
                ],
                db_index=True,
                max_length=20,
                verbose_name='角色',
            ),
        ),
        migrations.RunPython(forwards_sync_permissions, backwards_noop),
    ]
