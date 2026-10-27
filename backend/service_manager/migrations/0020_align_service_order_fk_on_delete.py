from django.db import migrations


# Django 模型是 SET_NULL / CASCADE，但早期 MySQL 外键落成了 NO ACTION，
# 已付款工单带账单、日志、预占时删除会被拦住。
_FK_FIXES = (
    (
        'customer_manager_customerbilling',
        'service_order_id',
        'customer_manager_cus_service_order_id_34f2230b_fk_service_m',
        'SET NULL',
    ),
    (
        'service_manager_popcapacityreservation',
        'service_order_id',
        'service_manager_popc_service_order_id_518d94a3_fk_service_m',
        'CASCADE',
    ),
    (
        'service_manager_servicechangerecord',
        'related_order_id',
        'service_manager_serv_related_order_id_31cc9d37_fk_service_m',
        'SET NULL',
    ),
    (
        'service_manager_serviceinquiry',
        'related_order_id',
        'service_manager_serv_related_order_id_0bd806e1_fk_service_m',
        'SET NULL',
    ),
    (
        'service_manager_servicelifecyclerecord',
        'service_order_id',
        'service_manager_serv_service_order_id_ccfeeebd_fk_service_m',
        'CASCADE',
    ),
    (
        'service_manager_servicemanualdelivery',
        'service_order_id',
        'service_manager_serv_service_order_id_f8e7f162_fk_service_m',
        'CASCADE',
    ),
    (
        'service_manager_serviceoperationlog',
        'service_order_id',
        'service_manager_serv_service_order_id_18d925d2_fk_service_m',
        'SET NULL',
    ),
)


def _forward(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    with schema_editor.connection.cursor() as cursor:
        for table, column, constraint, delete_rule in _FK_FIXES:
            cursor.execute(f'ALTER TABLE `{table}` DROP FOREIGN KEY `{constraint}`')
            cursor.execute(
                f'ALTER TABLE `{table}` '
                f'ADD CONSTRAINT `{constraint}` '
                f'FOREIGN KEY (`{column}`) '
                f'REFERENCES `service_manager_serviceorder` (`id`) '
                f'ON DELETE {delete_rule}'
            )


def _backward(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    with schema_editor.connection.cursor() as cursor:
        for table, column, constraint, _delete_rule in _FK_FIXES:
            cursor.execute(f'ALTER TABLE `{table}` DROP FOREIGN KEY `{constraint}`')
            cursor.execute(
                f'ALTER TABLE `{table}` '
                f'ADD CONSTRAINT `{constraint}` '
                f'FOREIGN KEY (`{column}`) '
                f'REFERENCES `service_manager_serviceorder` (`id`) '
                f'ON DELETE NO ACTION'
            )


class Migration(migrations.Migration):

    dependencies = [
        ('service_manager', '0019_rename_service_man_pop_poi_capres_idx_service_man_pop_poi_c1a4de_idx_and_more'),
        ('customer_manager', '0019_fill_reputation_balance_from_quota'),
    ]

    operations = [
        migrations.RunPython(_forward, _backward),
    ]
