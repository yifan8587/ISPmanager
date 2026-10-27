"""POP / IP / 隧道 Excel 导入导出。"""
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from openpyxl import Workbook, load_workbook
from rest_framework.test import APIClient

from customer_manager.models import Customer
from resource_manager.excel_ops import generate_block_ips
from resource_manager.models import (
    AvailabilityZone,
    Carrier,
    City,
    Country,
    IPAddress,
    IPBlock,
    PopPoint,
    Region,
    TunnelIPPool,
)


def _xlsx(sheets):
    wb = Workbook()
    first = True
    for name, headers, rows in sheets:
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = name
        ws.append(headers)
        for row in rows:
            ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    buf.name = 'data.xlsx'
    return buf


class ResourceExcelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('excel-res', 'excel-res@example.test', 'password')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.region = Region.objects.create(code='xlr', name='Excel资源区')
        self.country = Country.objects.create(region=self.region, name='Excel资源国')
        self.city = City.objects.create(country=self.country, name='Excel资源市')
        self.pop = PopPoint.objects.create(name='Excel资源POP', city=self.city, total_bandwidth_mbps=500)

    def test_pop_export_hides_passwords(self):
        self.pop.mgmt_password = 'secret-mgmt'
        self.pop.api_password = 'secret-api'
        self.pop.save(update_fields=['mgmt_password', 'api_password'])
        res = self.client.get('/api/resource/pop-points/export/')
        self.assertEqual(res.status_code, 200)
        wb = load_workbook(BytesIO(res.content))
        rows = list(wb['POP节点'].iter_rows(values_only=True))
        headers = [str(v) for v in rows[0]]
        names = [row[headers.index('节点名称')] for row in rows[1:]]
        self.assertIn('Excel资源POP', names)
        pwd_idx = headers.index('管理密码')
        api_idx = headers.index('API密码')
        for row in rows[1:]:
            if row[headers.index('节点名称')] == 'Excel资源POP':
                self.assertIn(row[pwd_idx], (None, ''))
                self.assertIn(row[api_idx], (None, ''))

    def test_pop_import_create_and_update(self):
        buf = _xlsx([('POP节点', [
            '节点编号', '节点名称', '节点类型', '状态', '城市', '总带宽Mbps', '备注',
        ], [
            ['', '导入节点A', 'POP', '在线', 'Excel资源国/Excel资源市', 200, ''],
        ])])
        res = self.client.post('/api/resource/pop-points/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['created'], 1)
        obj = PopPoint.objects.get(name='导入节点A')
        self.assertEqual(obj.city_id, self.city.id)
        self.assertEqual(obj.total_bandwidth_mbps, 200)

        buf = _xlsx([('POP节点', [
            '节点编号', '节点名称', '城市', '总带宽Mbps',
        ], [
            [obj.code, '导入节点A改名', 'Excel资源国/Excel资源市', 0],
        ])])
        res = self.client.post('/api/resource/pop-points/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        obj.refresh_from_db()
        self.assertEqual(obj.name, '导入节点A改名')
        self.assertEqual(obj.total_bandwidth_mbps, 0)

    def test_sheet_pop_code_is_kept_and_reimport_updates(self):
        buf = _xlsx([('POP节点', [
            '节点编号', '节点名称', '节点类型', '状态', '城市', '总带宽Mbps',
        ], [
            ['POP-KEEP-01', '编号节点', 'POP', '在线', 'Excel资源国/Excel资源市', 100],
        ])])
        res = self.client.post('/api/resource/pop-points/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['created'], 1)
        obj = PopPoint.objects.get(code='POP-KEEP-01')

        buf = _xlsx([('POP节点', [
            '节点编号', '节点名称', '城市', '总带宽Mbps',
        ], [
            ['POP-KEEP-01', '编号节点已改', 'Excel资源国/Excel资源市', 50],
        ])])
        res = self.client.post('/api/resource/pop-points/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['updated'], 1)
        self.assertEqual(res.data['created'], 0)
        self.assertEqual(PopPoint.objects.filter(code='POP-KEEP-01').count(), 1)
        obj.refresh_from_db()
        self.assertEqual(obj.name, '编号节点已改')
        self.assertEqual(obj.total_bandwidth_mbps, 50)

    def test_ip_block_import_generates_hosts(self):
        buf = _xlsx([('IP地址块', [
            'CIDR', 'POP编号', 'IP版本', '网关', '启用', '解析IP',
        ], [
            ['203.0.113.0/30', self.pop.code, 'v4', '203.0.113.1', '是', '是'],
        ])])
        res = self.client.post('/api/resource/ip-blocks/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['created'], 1)
        self.assertEqual(res.data['generated_ips'], 2)
        block = IPBlock.objects.get(pop_point=self.pop, cidr='203.0.113.0/30')
        addrs = set(block.ip_addresses.values_list('address', flat=True))
        self.assertEqual(addrs, {'203.0.113.1', '203.0.113.2'})

    def test_ip_address_import(self):
        block = IPBlock.objects.create(pop_point=self.pop, cidr='198.51.100.0/29')
        buf = _xlsx([('IP地址', [
            'IP地址', '所属CIDR', 'POP编号', '状态', '描述',
        ], [
            ['198.51.100.10', '198.51.100.0/29', self.pop.code, '可用', '手工导入'],
        ])])
        res = self.client.post('/api/resource/ip-addresses/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['created'], 1)
        ip = IPAddress.objects.get(address='198.51.100.10')
        self.assertEqual(ip.ip_block_id, block.id)
        self.assertEqual(ip.status, IPAddress.IPStatus.AVAILABLE)

        assigned = IPAddress.objects.create(
            ip_block=block, address='198.51.100.11', status=IPAddress.IPStatus.ASSIGNED,
        )
        buf = _xlsx([('IP地址', [
            'IP地址', '所属CIDR', 'POP编号', '状态', '描述',
        ], [
            ['198.51.100.11', '198.51.100.0/29', self.pop.code, '可用', '不应改已分配'],
        ])])
        res = self.client.post('/api/resource/ip-addresses/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        assigned.refresh_from_db()
        self.assertEqual(assigned.status, IPAddress.IPStatus.ASSIGNED)
        self.assertNotEqual(assigned.description, '不应改已分配')

    def test_ip_export_uses_filters(self):
        keep = IPBlock.objects.create(pop_point=self.pop, cidr='192.0.2.0/24')
        other = IPBlock.objects.create(pop_point=self.pop, cidr='198.51.100.0/24')
        IPAddress.objects.create(ip_block=keep, address='192.0.2.10')
        IPAddress.objects.create(ip_block=other, address='198.51.100.10')
        res = self.client.get('/api/resource/ip-addresses/export/', {'search': '192.0.2.10'})
        self.assertEqual(res.status_code, 200)
        wb = load_workbook(BytesIO(res.content))
        rows = list(wb['IP地址'].iter_rows(values_only=True))
        headers = [str(v) for v in rows[0]]
        addrs = [row[headers.index('IP地址')] for row in rows[1:] if row[headers.index('IP地址')]]
        self.assertIn('192.0.2.10', addrs)
        self.assertNotIn('198.51.100.10', addrs)

    def test_tunnel_import(self):
        customer = Customer.objects.create(name='Excel隧道客户', contact_name='联系人')
        buf = _xlsx([('隧道地址段', [
            '名称', '地址段CIDR', 'POP编号', 'p2p前缀', '客户编号', '启用', '备注',
        ], [
            ['edge-gre', '10.21.0.0/24', self.pop.code, 30, customer.customer_no, '是', ''],
        ])])
        res = self.client.post('/api/resource/tunnel-ip-pools/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['created'], 1)
        pool = TunnelIPPool.objects.get(pop_point=self.pop, name='edge-gre')
        self.assertEqual(pool.cidr, '10.21.0.0/24')
        self.assertEqual(pool.customer_id, customer.id)

    def test_base_data_roundtrip(self):
        res = self.client.get('/api/resource/base-data/export/')
        self.assertEqual(res.status_code, 200)
        wb = load_workbook(BytesIO(res.content))
        for name in ('大区', '国家', '城市', '可用区', '运营商'):
            self.assertIn(name, wb.sheetnames)
        buf = _xlsx([
            ('大区', ['区域代码', '名称', '英文名称'], [
                ['europe', 'Excel导入欧洲', 'Excel Europe'],
            ]),
            ('国家', ['国家名称', '英文名称', '所属大区'], [
                ['Excel导入国', 'ExcelLand', 'Excel导入欧洲'],
            ]),
            ('城市', ['城市名称', '英文名称', '国家', '大区', '时区'], [
                ['Excel导入市', 'ExcelCity', 'Excel导入国', 'Excel导入欧洲', 'Europe/Paris'],
            ]),
            ('可用区', ['可用区名称', '代码', '城市', '国家', '大区'], [
                ['Excel导入AZ', 'excel-az', 'Excel导入市', 'Excel导入国', 'Excel导入欧洲'],
            ]),
            ('运营商', ['运营商代码', '名称', '覆盖大区'], [
                ['EXL', 'Excel Carrier', 'Excel导入欧洲'],
            ]),
        ])
        res = self.client.post('/api/resource/base-data/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertGreaterEqual(res.data['created'], 5)
        region = Region.objects.get(code='europe')
        self.assertEqual(region.name, 'Excel导入欧洲')
        country = Country.objects.get(name='Excel导入国')
        self.assertEqual(country.region_id, region.id)
        city = City.objects.get(name='Excel导入市')
        self.assertEqual(city.country_id, country.id)
        zone = AvailabilityZone.objects.get(name='Excel导入AZ')
        self.assertEqual(zone.city_id, city.id)
        carrier = Carrier.objects.get(code='EXL')
        self.assertEqual(list(carrier.regions.values_list('id', flat=True)), [region.id])

    def test_pop_import_by_location_columns(self):
        zone = AvailabilityZone.objects.create(city=self.city, name='Excel AZ', code='excel-az')
        buf = _xlsx([('POP节点', [
            '节点名称', '大区', '国家', '城市', '可用区', '总带宽Mbps',
        ], [
            ['按位置导入POP', 'Excel资源区', 'Excel资源国', 'Excel资源市', 'excel-az', 300],
        ])])
        res = self.client.post('/api/resource/pop-points/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['created'], 1)
        obj = PopPoint.objects.get(name='按位置导入POP')
        self.assertEqual(obj.city_id, self.city.id)
        self.assertEqual(obj.availability_zone_id, zone.id)

    def test_ip_and_tunnel_bind_pop_location(self):
        buf = _xlsx([('IP地址块', [
            'CIDR', 'POP名称', '大区', '国家', '城市', '启用',
        ], [
            ['198.51.100.0/30', self.pop.name, 'Excel资源区', 'Excel资源国', 'Excel资源市', '是'],
        ])])
        res = self.client.post('/api/resource/ip-blocks/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['created'], 1)
        self.assertTrue(IPBlock.objects.filter(pop_point=self.pop, cidr='198.51.100.0/30').exists())

        other_city = City.objects.create(country=self.country, name='Excel另一市')
        buf = _xlsx([('IP地址块', [
            'CIDR', 'POP编号', '城市',
        ], [
            ['203.0.113.8/30', self.pop.code, other_city.name],
        ])])
        res = self.client.post('/api/resource/ip-blocks/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['errors'])
        self.assertFalse(IPBlock.objects.filter(cidr='203.0.113.8/30').exists())

        buf = _xlsx([('隧道地址段', [
            '名称', '地址段CIDR', 'POP名称', '大区', '国家', '城市', 'p2p前缀', '启用',
        ], [
            ['loc-tun', '10.31.0.0/24', self.pop.name, 'Excel资源区', 'Excel资源国', 'Excel资源市', 30, '是'],
        ])])
        res = self.client.post('/api/resource/tunnel-ip-pools/import/', {'file': buf}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['created'], 1)
        self.assertTrue(TunnelIPPool.objects.filter(pop_point=self.pop, name='loc-tun').exists())

    def test_generate_rejects_huge_ipv6_without_listing(self):
        block = IPBlock.objects.create(pop_point=self.pop, cidr='2001:db8::/64', ip_version='v6')
        with patch('ipaddress.IPv6Network.__iter__', side_effect=AssertionError('should not iterate')):
            with self.assertRaises(ValueError):
                generate_block_ips(block)
