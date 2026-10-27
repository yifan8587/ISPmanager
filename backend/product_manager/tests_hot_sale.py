"""产品热卖：手动勾选，或满 2 个在服服务自动提示。"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from customer_manager.models import Customer
from product_manager.hot_sale import HOT_SALE_SOLD_THRESHOLD
from product_manager.models import Product
from product_manager.serializers import ProductListSerializer
from resource_manager.models import City, Country, PopPoint, Region
from service_manager.models import ServiceInstance, ServiceOrder
from supplier_manager.models import Supplier


class ProductHotSaleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('hot-sale', 'hot-sale@example.test', 'password')
        self.customer = Customer.objects.create(name='热卖客户', contact_name='联系人', status='active')
        region = Region.objects.create(code='hot', name='热卖区')
        country = Country.objects.create(region=region, name='热卖国')
        city = City.objects.create(country=country, name='热卖市')
        self.pop = PopPoint.objects.create(name='热卖POP', city=city)
        self.supplier = Supplier.objects.create(name='示例供应商', short_name='示例', supplier_type='idc', country='香港')
        self.product = Product.objects.create(
            name='热卖测试产品',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('10.00'),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _instance(self, status=ServiceInstance.InstanceStatus.ACTIVE):
        order = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            created_by=self.user,
        )
        return ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.VPS,
            status=status,
        )

    def test_default_is_not_hot_sale(self):
        self.assertFalse(self.product.is_hot_sale)
        data = ProductListSerializer(self.product).data
        self.assertFalse(data['is_hot_sale'])
        self.assertFalse(data['hot_sale'])
        self.assertEqual(data['sold_service_count'], 0)
        self.assertIsNone(data['supplier_info'])

    def test_manual_flag_marks_hot_sale(self):
        self.product.is_hot_sale = True
        self.product.save(update_fields=['is_hot_sale'])
        data = ProductListSerializer(self.product).data
        self.assertTrue(data['is_hot_sale'])
        self.assertTrue(data['hot_sale'])

    def test_two_active_services_auto_hot_sale(self):
        self._instance()
        self.assertFalse(ProductListSerializer(self.product).data['hot_sale'])
        self._instance()
        data = ProductListSerializer(self.product).data
        self.assertEqual(data['sold_service_count'], HOT_SALE_SOLD_THRESHOLD)
        self.assertTrue(data['hot_sale'])
        self.assertFalse(data['is_hot_sale'])

    def test_terminated_services_do_not_count(self):
        self._instance(ServiceInstance.InstanceStatus.TERMINATED)
        self._instance(ServiceInstance.InstanceStatus.EXPIRED)
        data = ProductListSerializer(self.product).data
        self.assertEqual(data['sold_service_count'], 0)
        self.assertFalse(data['hot_sale'])

    def test_supplier_info_and_write_api(self):
        self.product.supplier = self.supplier
        self.product.save(update_fields=['supplier'])
        data = ProductListSerializer(self.product).data
        self.assertEqual(data['supplier_name'], '示例供应商')
        self.assertEqual(data['supplier_info']['name'], '示例供应商')
        self.assertEqual(data['supplier_info']['type_label'], 'IDC 机房')
        self.assertEqual(data['supplier_info']['country'], '香港')

        res = self.client.patch(
            f'/api/product/products/{self.product.pk}/',
            {'is_hot_sale': True},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_hot_sale)

    def test_portal_list_exposes_hot_sale(self):
        self._instance()
        self._instance()
        guest = APIClient()
        res = guest.get('/api/portal/products/')
        self.assertEqual(res.status_code, 200)
        row = next(item for item in res.data['results'] if item['id'] == self.product.pk)
        self.assertTrue(row['hot_sale'])
        self.assertEqual(row['sold_service_count'], 2)
