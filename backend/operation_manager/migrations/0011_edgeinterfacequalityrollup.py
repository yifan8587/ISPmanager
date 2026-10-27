from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('customer_manager', '0013_channelcommission_billing_amount_and_more'),
        ('operation_manager', '0010_poptelemetrycredential_edgeinterfacequalitysample'),
        ('resource_manager', '0024_edge_tunnel_mtu'),
    ]

    operations = [
        migrations.CreateModel(
            name='EdgeInterfaceQualityRollup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('bucket_kind', models.CharField(choices=[('hour', '小时'), ('day', '天')], max_length=8, verbose_name='聚合粒度')),
                ('bucket_ts', models.DateTimeField(verbose_name='桶起点')),
                ('sample_count', models.PositiveIntegerField(default=0, verbose_name='样本数')),
                ('ok_samples', models.PositiveIntegerField(default=0, verbose_name='成功样本数')),
                ('latency_avg_ms', models.FloatField(blank=True, null=True, verbose_name='平均延迟(ms)')),
                ('latency_max_ms', models.FloatField(blank=True, null=True, verbose_name='最大延迟(ms)')),
                ('packet_loss_avg_pct', models.FloatField(blank=True, null=True, verbose_name='平均丢包率(%)')),
                ('jitter_avg_ms', models.FloatField(blank=True, null=True, verbose_name='平均抖动(ms)')),
                ('availability_pct', models.FloatField(blank=True, null=True, verbose_name='可用性(%)')),
                ('last_sampled_at', models.DateTimeField(blank=True, null=True, verbose_name='桶内最新采样')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='edge_quality_rollups', to='customer_manager.customer', verbose_name='客户')),
                ('edge', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='quality_rollups', to='resource_manager.edgeacceleration', verbose_name='边缘加速')),
                ('pop', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='edge_quality_rollups', to='resource_manager.poppoint', verbose_name='上报 POP')),
            ],
            options={
                'verbose_name': '边缘接口品质汇总',
                'verbose_name_plural': '边缘接口品质汇总',
                'ordering': ['-bucket_ts', '-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='edgeinterfacequalityrollup',
            constraint=models.UniqueConstraint(fields=('pop', 'edge', 'bucket_kind', 'bucket_ts'), name='uniq_edge_quality_rollup_bucket'),
        ),
        migrations.AddIndex(
            model_name='edgeinterfacequalityrollup',
            index=models.Index(fields=['edge', 'bucket_kind', 'bucket_ts'], name='operation_m_edge_id_63da66_idx'),
        ),
        migrations.AddIndex(
            model_name='edgeinterfacequalityrollup',
            index=models.Index(fields=['customer', 'bucket_kind', 'bucket_ts'], name='operation_m_custome_d1da12_idx'),
        ),
    ]
