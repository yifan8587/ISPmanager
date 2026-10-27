import django.db.models.deletion
from django.db import migrations, models


ROLE_CHOICES = [
    ('superadmin', '超级管理员'),
    ('admin', '管理员'),
    ('customer_manager', '客户管理员'),
    ('operator', '运维人员'),
    ('sales', '销售人员'),
    ('customer', '客户用户'),
    ('supplier', '供应商'),
    ('channel', '渠道'),
    ('readonly', '只读用户'),
]


def forwards_sync_permissions(apps, schema_editor):
    from accounts.permission_service import sync_permission_catalog
    sync_permission_catalog()


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_supplier_role_and_profile'),
        ('customer_manager', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='channel',
            field=models.ForeignKey(
                blank=True,
                help_text='角色为「渠道」时必填，账号仅能维护该渠道名下客户、下单并查看佣金结算',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='channel_accounts',
                to='customer_manager.customer',
                verbose_name='绑定渠道客户',
            ),
        ),
        migrations.AlterField(
            model_name='userprofile',
            name='role',
            field=models.CharField(
                choices=ROLE_CHOICES,
                default='operator',
                max_length=20,
                verbose_name='系统角色',
            ),
        ),
        migrations.AlterField(
            model_name='rolepermissiongrant',
            name='role',
            field=models.CharField(
                choices=ROLE_CHOICES,
                db_index=True,
                max_length=20,
                verbose_name='角色',
            ),
        ),
        migrations.RunPython(forwards_sync_permissions, backwards_noop),
    ]
