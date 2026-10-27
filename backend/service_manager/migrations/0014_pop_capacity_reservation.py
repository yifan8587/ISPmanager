import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resource_manager', '0025_poppoint_allow_oversell'),
        ('service_manager', '0013_order_fx_snapshot'),
    ]

    operations = [
        migrations.CreateModel(
            name='PopCapacityReservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(
                    choices=[('held', '预占'), ('consumed', '已消耗'), ('released', '已释放')],
                    default='held',
                    max_length=16,
                    verbose_name='状态',
                )),
                ('cpu_cores', models.PositiveIntegerField(default=0, verbose_name='预占 vCPU')),
                ('ram_mb', models.PositiveIntegerField(default=0, verbose_name='预占内存(MB)')),
                ('disk_gb', models.PositiveIntegerField(default=0, verbose_name='预占硬盘(GB)')),
                ('bandwidth_mbps', models.PositiveIntegerField(default=0, verbose_name='预占带宽(Mbps)')),
                ('ip_count', models.PositiveIntegerField(default=0, verbose_name='预占 IP 数')),
                ('expires_at', models.DateTimeField(blank=True, null=True, verbose_name='预占到期时间')),
                ('held_at', models.DateTimeField(auto_now_add=True, verbose_name='预占时间')),
                ('consumed_at', models.DateTimeField(blank=True, null=True, verbose_name='消耗时间')),
                ('released_at', models.DateTimeField(blank=True, null=True, verbose_name='释放时间')),
                ('release_reason', models.CharField(blank=True, max_length=255, verbose_name='释放原因')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('pop_point', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='capacity_reservations',
                    to='resource_manager.poppoint',
                    verbose_name='POP 节点',
                )),
                ('service_order', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='capacity_reservations',
                    to='service_manager.serviceorder',
                    verbose_name='服务工单',
                )),
            ],
            options={
                'verbose_name': 'POP 容量预占',
                'verbose_name_plural': 'POP 容量预占',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='popcapacityreservation',
            index=models.Index(fields=['pop_point', 'status'], name='service_man_pop_poi_capres_idx'),
        ),
        migrations.AddIndex(
            model_name='popcapacityreservation',
            index=models.Index(fields=['status', 'expires_at'], name='service_man_status_capexp_idx'),
        ),
        migrations.AddIndex(
            model_name='popcapacityreservation',
            index=models.Index(fields=['service_order', 'status'], name='service_man_order_capst_idx'),
        ),
        migrations.AddConstraint(
            model_name='popcapacityreservation',
            constraint=models.UniqueConstraint(
                condition=models.Q(status='held'),
                fields=('service_order',),
                name='uniq_held_capacity_reservation_per_order',
            ),
        ),
    ]
