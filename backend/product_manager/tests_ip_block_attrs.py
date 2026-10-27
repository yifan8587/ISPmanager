"""产品网络类型 / ASN / IP 属性从所属 POP 点读取。"""
from decimal import Decimal

from django.test import TestCase

from product_manager.models import Product
from product_manager.serializers import ProductListSerializer
from product_manager.views import ProductFilter
from resource_manager.models import (
    AsnNature, City, Country, IPAddress, IPAttribute, IPBlock, NetworkTypeOption, PopPoint, Region,
)


class ProductIpBlockAttrTests(TestCase):
    def setUp(self):
        NetworkTypeOption.objects.get_or_create(
            code='idc', defaults={'name': 'IDC', 'sort_order': 0},
        )
        NetworkTypeOption.objects.get_or_create(
            code='home_broadband', defaults={'name': '家宽', 'sort_order': 1},
        )
        region = Region.objects.create(code='ip-attr', name='属性区')
        country = Country.objects.create(region=region, name='属性国')
        city = City.objects.create(country=country, name='属性市')
        self.pop = PopPoint.objects.create(
            name='属性 POP', city=city,
            network_type='idc',
            asn_nature=AsnNature.ISP,
        )
        self.product = Product.objects.create(
            name='属性产品',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('10.00'),
        )

    def test_ip_block_defaults_to_single_isp_and_idc(self):
        block = IPBlock.objects.create(pop_point=self.pop, cidr='203.0.113.0/24')
        self.assertEqual(block.network_type, 'idc')
        self.assertEqual(block.asn_nature, AsnNature.ISP)
        self.assertEqual(block.get_asn_nature_display(), '单ISP')

    def _add_address(self, block, host='198.51.100.10'):
        return IPAddress.objects.create(ip_block=block, address=host)

    def test_pop_ip_attribute_defaults_to_native(self):
        self.assertEqual(self.pop.ip_attribute, IPAttribute.NATIVE)
        self.assertEqual(self.pop.get_ip_attribute_display(), '原生')

    def test_product_list_reads_pop_not_ip_blocks(self):
        self.pop.network_type = 'home_broadband'
        self.pop.asn_nature = AsnNature.DUAL_ISP
        self.pop.ip_attribute = IPAttribute.BROADCAST
        self.pop.save(update_fields=['network_type', 'asn_nature', 'ip_attribute'])
        block = IPBlock.objects.create(
            pop_point=self.pop, cidr='198.51.100.0/24',
            network_type='idc', asn_nature=AsnNature.ISP,
            ip_attribute=IPAttribute.NATIVE,
        )
        self._add_address(block)
        data = ProductListSerializer(self.product).data
        self.assertEqual(data['network_type'], 'home_broadband')
        self.assertEqual(data['network_type_label'], '家宽')
        self.assertEqual(data['asn_nature'], AsnNature.DUAL_ISP)
        self.assertEqual(data['asn_nature_label'], '双ISP')
        self.assertEqual([x['value'] for x in data['network_types']], ['home_broadband'])
        self.assertEqual([x['value'] for x in data['asn_natures']], [AsnNature.DUAL_ISP])
        self.assertEqual(data['ip_attributes'], [IPAttribute.BROADCAST])

    def test_product_ignores_ip_block_attrs(self):
        IPBlock.objects.create(
            pop_point=self.pop, cidr='203.0.113.0/24',
            network_type='home_broadband', asn_nature=AsnNature.NON_ISP,
            ip_attribute=IPAttribute.BROADCAST,
        )
        data = ProductListSerializer(self.product).data
        self.assertEqual(data['network_type'], 'idc')
        self.assertEqual(data['asn_nature'], AsnNature.ISP)
        self.assertEqual(data['ip_attributes'], [IPAttribute.NATIVE])

    def test_product_filter_uses_pop(self):
        self.pop.network_type = 'home_broadband'
        self.pop.asn_nature = AsnNature.NON_ISP
        self.pop.ip_attribute = IPAttribute.BROADCAST
        self.pop.save(update_fields=['network_type', 'asn_nature', 'ip_attribute'])
        block = IPBlock.objects.create(
            pop_point=self.pop, cidr='198.51.100.0/24',
            network_type='idc', asn_nature=AsnNature.ISP,
            ip_attribute=IPAttribute.NATIVE,
        )
        self._add_address(block)
        qs = Product.objects.all()
        self.assertEqual(ProductFilter({'network_type': 'home_broadband'}, queryset=qs).qs.count(), 1)
        self.assertEqual(ProductFilter({'network_type': 'idc'}, queryset=qs).qs.count(), 0)
        self.assertEqual(ProductFilter({'asn_nature': 'non_isp'}, queryset=qs).qs.count(), 1)
        self.assertEqual(ProductFilter({'asn_nature': 'isp'}, queryset=qs).qs.count(), 0)
        self.assertEqual(ProductFilter({'ip_attribute': 'broadcast'}, queryset=qs).qs.count(), 1)
        self.assertEqual(ProductFilter({'ip_attribute': 'native'}, queryset=qs).qs.count(), 0)

    def test_product_without_ip_blocks_uses_pop_defaults(self):
        data = ProductListSerializer(self.product).data
        self.assertEqual(data['network_type'], 'idc')
        self.assertEqual(data['asn_nature'], AsnNature.ISP)
        self.assertEqual([x['value'] for x in data['network_types']], ['idc'])
        self.assertEqual([x['value'] for x in data['asn_natures']], [AsnNature.ISP])
        self.assertEqual(data['ip_attributes'], [IPAttribute.NATIVE])
