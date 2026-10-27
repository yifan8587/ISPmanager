from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('resource_manager', '0026_poppoint_api_verify_ssl'),
    ]

    operations = [
        migrations.CreateModel(
            name='AvailabilityZone',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=64, verbose_name='可用区名称')),
                ('name_en', models.CharField(blank=True, max_length=64, verbose_name='英文名称')),
                ('code', models.CharField(blank=True, default='', help_text='可选，如 tokyo-a；同一城市内不重复', max_length=32, verbose_name='可用区代码')),
                ('remark', models.CharField(blank=True, default='', max_length=256, verbose_name='备注')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('city', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='availability_zones', to='resource_manager.city', verbose_name='所属城市')),
            ],
            options={
                'verbose_name': '可用区',
                'verbose_name_plural': '可用区列表',
                'ordering': ['city', 'name'],
            },
        ),
        migrations.AddConstraint(
            model_name='availabilityzone',
            constraint=models.UniqueConstraint(fields=('city', 'name'), name='uniq_az_city_name'),
        ),
        migrations.AddField(
            model_name='poppoint',
            name='availability_zone',
            field=models.ForeignKey(
                blank=True,
                help_text='可选，须属于所在城市',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='pop_points',
                to='resource_manager.availabilityzone',
                verbose_name='可用区',
            ),
        ),
        migrations.AlterField(
            model_name='resourceoperationlog',
            name='target_model',
            field=models.CharField(
                blank=True,
                choices=[
                    ('PopPoint', 'POP 点'),
                    ('VPS', 'VPS'),
                    ('EdgeAcceleration', '边缘加速'),
                    ('IPBlock', 'IP 地址块'),
                    ('IPAddress', 'IP 地址'),
                    ('NetworkInterface', '网络接口'),
                    ('RouteTable', '路由表'),
                    ('PolicyRoute', '策略路由'),
                    ('BandwidthAllocation', '带宽分配'),
                    ('Carrier', '运营商'),
                    ('Region', '大区'),
                    ('Country', '国家'),
                    ('City', '城市'),
                    ('AvailabilityZone', '可用区'),
                    ('Other', '其他'),
                ],
                max_length=32,
                verbose_name='操作对象类型',
            ),
        ),
    ]
