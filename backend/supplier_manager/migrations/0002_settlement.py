from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('customer_manager', '0019_fill_reputation_balance_from_quota'),
        ('product_manager', '0009_product_is_hot_sale'),
        ('service_manager', '0021_os_template_catalog'),
        ('supplier_manager', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplier',
            name='settlement_min_amount',
            field=models.DecimalField(
                decimal_places=2, default=100, max_digits=14,
                verbose_name='满额结算金额',
                help_text='待结算金额达到该阈值后可发起结算；0 表示不自动满额、需手工结算',
            ),
        ),
        migrations.AddField(
            model_name='supplier',
            name='settlement_rate',
            field=models.DecimalField(
                decimal_places=2, default=90, max_digits=5,
                verbose_name='结算比例(%)',
                help_text='用户实付金额中结算给供应商的比例，例如 90 表示结算 90%',
            ),
        ),
        migrations.CreateModel(
            name='SupplierSettlement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('settlement_no', models.CharField(editable=False, max_length=32, unique=True, verbose_name='结算单号')),
                ('amount', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='结算金额')),
                ('currency', models.CharField(default='CNY', max_length=8, verbose_name='币种')),
                ('bill_count', models.PositiveIntegerField(default=0, verbose_name='账单笔数')),
                ('status', models.CharField(
                    choices=[('pending', '待打款'), ('paid', '已结算'), ('cancelled', '已取消')],
                    default='pending', max_length=16, verbose_name='状态',
                )),
                ('auto_created', models.BooleanField(default=False, verbose_name='满额自动生成')),
                ('remark', models.CharField(blank=True, max_length=256, verbose_name='备注')),
                ('settled_at', models.DateTimeField(blank=True, null=True, verbose_name='结算时间')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('created_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_supplier_settlements', to=settings.AUTH_USER_MODEL,
                    verbose_name='发起人',
                )),
                ('settled_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='paid_supplier_settlements', to=settings.AUTH_USER_MODEL,
                    verbose_name='确认人',
                )),
                ('supplier', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='settlements', to='supplier_manager.supplier',
                    verbose_name='供应商',
                )),
            ],
            options={
                'verbose_name': '供应商结算单',
                'verbose_name_plural': '供应商结算单',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='SupplierBill',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('bill_no', models.CharField(editable=False, max_length=32, unique=True, verbose_name='供应商账单号')),
                ('customer_bill_no', models.CharField(blank=True, max_length=64, verbose_name='客户账单号')),
                ('customer_name', models.CharField(blank=True, max_length=128, verbose_name='客户名称')),
                ('product_name', models.CharField(blank=True, max_length=128, verbose_name='产品名称')),
                ('bill_month', models.CharField(blank=True, max_length=7, verbose_name='账期')),
                ('source_amount', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='用户实付金额')),
                ('settlement_rate', models.DecimalField(decimal_places=2, default=90, max_digits=5, verbose_name='结算比例(%)')),
                ('settle_amount', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='应结金额')),
                ('currency', models.CharField(default='CNY', max_length=8, verbose_name='币种')),
                ('status', models.CharField(
                    choices=[
                        ('pending', '待结算'), ('in_settlement', '结算中'),
                        ('settled', '已结算'), ('cancelled', '已取消'),
                    ],
                    default='pending', max_length=16, verbose_name='状态',
                )),
                ('paid_at', models.DateTimeField(blank=True, null=True, verbose_name='客户支付时间')),
                ('remark', models.CharField(blank=True, max_length=256, verbose_name='备注')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('billing_item', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='supplier_bills', to='customer_manager.billingitem',
                    verbose_name='客户账单明细',
                )),
                ('customer_billing', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='supplier_bills', to='customer_manager.customerbilling',
                    verbose_name='客户账单',
                )),
                ('product', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='supplier_bills', to='product_manager.product',
                    verbose_name='产品',
                )),
                ('service_order', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='supplier_bills', to='service_manager.serviceorder',
                    verbose_name='服务工单',
                )),
                ('settlement', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='bills', to='supplier_manager.suppliersettlement',
                    verbose_name='所属结算单',
                )),
                ('supplier', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='bills', to='supplier_manager.supplier',
                    verbose_name='供应商',
                )),
            ],
            options={
                'verbose_name': '供应商账单',
                'verbose_name_plural': '供应商账单',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='suppliersettlement',
            index=models.Index(fields=['supplier', 'status'], name='supplier_ma_supplie_settl_idx'),
        ),
        migrations.AddIndex(
            model_name='supplierbill',
            index=models.Index(fields=['supplier', 'status'], name='supplier_ma_supplie_bill_idx'),
        ),
        migrations.AddIndex(
            model_name='supplierbill',
            index=models.Index(fields=['customer_billing'], name='supplier_ma_custbil_idx'),
        ),
        migrations.AddIndex(
            model_name='supplierbill',
            index=models.Index(fields=['bill_month'], name='supplier_ma_billmon_idx'),
        ),
        migrations.AddConstraint(
            model_name='supplierbill',
            constraint=models.UniqueConstraint(
                condition=models.Q(('billing_item__isnull', False)),
                fields=('billing_item',),
                name='uniq_supplier_bill_item',
            ),
        ),
        migrations.AddConstraint(
            model_name='supplierbill',
            constraint=models.UniqueConstraint(
                condition=models.Q(('billing_item__isnull', True), ('product__isnull', False)),
                fields=('customer_billing', 'product'),
                name='uniq_supplier_bill_billing_product',
            ),
        ),
    ]
