from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from product_manager.models import Product
from resource_manager.models import City, Country, PopPoint, Region
from service_manager.models import OsImage
from system_manager.models import SystemOsTemplate
from system_manager.os_templates import (
    annotate_os_templates_for_portal,
    match_os_template,
    product_os_templates,
    upsert_os_template_from_pop_item,
)
from resource_manager.pve_ops import dispatch_pop_os_image_downloads


class SystemOsTemplateCatalogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('os-admin', 'os@example.test', 'password')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        region = Region.objects.create(code='os-apac', name='系统模版区')
        country = Country.objects.create(region=region, name='系统模版国')
        city = City.objects.create(country=country, name='系统模版市')
        self.pop = PopPoint.objects.create(name='系统模版 POP', city=city)

    def test_create_syncs_os_image_and_lists_for_product_portal(self):
        response = self.client.post('/api/system/os-templates/', {
            'name': 'Ubuntu',
            'version': '22.04',
            'backup_filename': 'ubuntu-22.04-server-cloudimg-amd64.img',
            'download_url': 'http://mirror.example.com/ubuntu-22.04-server-cloudimg-amd64.img',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        template = SystemOsTemplate.objects.get(pk=response.data['id'])
        self.assertEqual(template.os_type, 'ubuntu')
        self.assertEqual(template.code, 'ubuntu-2204')
        image = OsImage.objects.get(os_template=template)
        self.assertEqual(image.name, 'Ubuntu 22.04')
        self.assertEqual(image.node_template_ref, 'ubuntu-22.04-server-cloudimg-amd64.img')
        self.assertTrue(image.enabled)

        product = Product.objects.create(
            name='目录 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
        )
        catalog = self.client.get('/api/product/products/os-templates/')
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.data[0]['label'], 'Ubuntu 22.04')

        portal = self.client.get('/api/portal/os-templates/')
        self.assertEqual(portal.status_code, 200)
        self.assertEqual(portal.data['data'][0]['id'], template.pk)

        product_portal = self.client.get(f'/api/portal/os-templates/?product_id={product.pk}')
        self.assertEqual(product_portal.status_code, 200)
        self.assertEqual(len(product_portal.data['data']), 1)

    def test_product_binding_filters_portal_catalog(self):
        ubuntu = SystemOsTemplate.objects.create(
            name='Ubuntu',
            version='22.04',
            backup_filename='ubuntu-22.04-server-cloudimg-amd64.img',
            download_url='http://mirror.example.com/ubuntu-22.04-server-cloudimg-amd64.img',
        )
        debian = SystemOsTemplate.objects.create(
            name='Debian',
            version='12',
            backup_filename='debian-12-genericcloud-amd64.qcow2',
            download_url='http://mirror.example.com/debian-12-genericcloud-amd64.qcow2',
        )
        product = Product.objects.create(
            name='仅 Debian VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
        )
        product.os_templates.set([debian])
        labels = [item.display_label for item in product_os_templates(product)]
        self.assertEqual(labels, ['Debian 12'])
        self.assertEqual(match_os_template('Ubuntu 22.04').pk, ubuntu.pk)
        self.assertEqual(match_os_template(debian.pk).pk, debian.pk)

        detail = self.client.get(f'/api/portal/products/{product.pk}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual([item['id'] for item in detail.data['os_templates']], [debian.pk])
        self.assertEqual(detail.data['os_template_ids'], [debian.pk])

    def test_non_system_account_cannot_manage(self):
        user = User.objects.create_user('os-staff', password='SafePass_2026!')
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.save(update_fields=['account_type'])
        client = APIClient()
        client.force_authenticate(user)
        response = client.post('/api/system/os-templates/', {
            'name': 'Debian',
            'version': '11',
            'download_url': 'http://mirror.example.com/debian-11-genericcloud-amd64.qcow2',
        }, format='json')
        self.assertIn(response.status_code, (403, 401))

    def test_upsert_and_annotate_pop_catalog(self):
        created = upsert_os_template_from_pop_item({
            'code': 'ubuntu-2404',
            'name': 'Ubuntu 24.04 (Noble)',
            'url': 'http://mirror.example.com/noble-server-cloudimg-amd64.img',
            'filename': 'noble-server-cloudimg-amd64.img',
            'default_user': 'ubuntu',
            'ready': True,
        })
        self.assertIsNotNone(created)
        self.assertEqual(created.code, 'ubuntu-2404')
        again = upsert_os_template_from_pop_item({
            'code': 'ubuntu-2404',
            'name': 'Ubuntu 24.04 (Noble)',
            'url': 'http://mirror.example.com/noble-server-cloudimg-amd64.img',
            'filename': 'noble-server-cloudimg-amd64.img',
        })
        self.assertEqual(again.pk, created.pk)
        rows = annotate_os_templates_for_portal([created], [{
            'code': 'ubuntu-2404',
            'filename': 'noble-server-cloudimg-amd64.img',
            'default_user': 'ubuntu',
            'ready': True,
        }])
        self.assertEqual(rows[0]['default_user'], 'ubuntu')
        self.assertTrue(rows[0]['ready_on_pop'])
        self.assertFalse(rows[0]['will_download'])
        missing = annotate_os_templates_for_portal([created], [])
        self.assertFalse(missing[0]['ready_on_pop'])
        self.assertTrue(missing[0]['will_download'])

    def test_portal_os_templates_mark_ready_from_pop_catalog(self):
        template = SystemOsTemplate.objects.create(
            name='Ubuntu',
            version='24.04',
            code='ubuntu-2404',
            backup_filename='noble-server-cloudimg-amd64.img',
            download_url='http://mirror.example.com/noble-server-cloudimg-amd64.img',
        )
        product = Product.objects.create(
            name='Cloud 同步 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
        )
        catalog = [{
            'code': 'ubuntu-2404',
            'name': 'Ubuntu 24.04 (Noble)',
            'filename': 'noble-server-cloudimg-amd64.img',
            'url': template.download_url,
            'default_user': 'ubuntu',
            'ready': True,
        }]
        with patch('resource_manager.pve_ops.fetch_pop_os_catalog', return_value=catalog):
            response = self.client.get(f'/api/portal/os-templates/?product_id={product.pk}')
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['data'][0]
        self.assertEqual(row['id'], template.pk)
        self.assertTrue(row['ready_on_pop'])
        self.assertFalse(row['will_download'])
        self.assertEqual(row['default_user'], 'ubuntu')

    def test_portal_sync_creates_missing_cloud_template(self):
        product = Product.objects.create(
            name='自动入库 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
        )
        catalog = [{
            'code': 'debian-13',
            'name': 'Debian 13 (Trixie)',
            'filename': 'debian-13-genericcloud-amd64.qcow2',
            'url': 'http://mirror.example.com/debian-13-genericcloud-amd64.qcow2',
            'ready': False,
            'default_user': 'debian',
        }]
        with patch('resource_manager.pve_ops.fetch_pop_os_catalog', return_value=catalog):
            response = self.client.get(f'/api/portal/os-templates/?product_id={product.pk}')
        self.assertEqual(response.status_code, 200, response.data)
        created = SystemOsTemplate.objects.get(code='debian-13')
        self.assertEqual(created.download_url, catalog[0]['url'])
        row = next(item for item in response.data['data'] if item['code'] == 'debian-13')
        self.assertFalse(row['ready_on_pop'])
        self.assertTrue(row['will_download'])
        self.assertEqual(row['default_user'], 'debian')

    def test_admin_sync_from_pop(self):
        catalog = [{
            'code': 'almalinux-9',
            'name': 'AlmaLinux 9',
            'filename': 'AlmaLinux-9-GenericCloud-latest.x86_64.qcow2',
            'url': 'http://mirror.example.com/AlmaLinux-9-GenericCloud-latest.x86_64.qcow2',
            'ready': True,
            'default_user': 'almalinux',
        }]
        with patch('resource_manager.pve_ops.fetch_pop_os_catalog', return_value=catalog):
            response = self.client.post(
                '/api/system/os-templates/sync_from_pop/',
                {'pop_id': self.pop.pk},
                format='json',
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['created_count'], 1)
        self.assertIn('almalinux-9', response.data['created_codes'])
        self.assertTrue(SystemOsTemplate.objects.filter(code='almalinux-9').exists())

    def test_upsert_upgrades_vma_same_name_version(self):
        stale = SystemOsTemplate.objects.create(
            name='Ubuntu',
            version='22.04',
            code='ubuntu-2204',
            backup_filename='ubuntu-22.04.vma.zst',
            download_url='http://old.example.com/ubuntu-22.04.vma.zst',
            source_type=SystemOsTemplate.SourceType.BACKUP,
        )
        upgraded = upsert_os_template_from_pop_item({
            'code': 'ubuntu-22.04',
            'name': 'Ubuntu 22.04 (Jammy)',
            'url': 'http://mirror.example.com/jammy-server-cloudimg-amd64.img',
            'filename': 'jammy-server-cloudimg-amd64.img',
        })
        stale.refresh_from_db()
        self.assertEqual(upgraded.pk, stale.pk)
        self.assertEqual(stale.backup_filename, 'jammy-server-cloudimg-amd64.img')
        self.assertEqual(stale.source_type, SystemOsTemplate.SourceType.CLOUD)
        self.assertFalse(stale.backup_filename.endswith('.vma.zst'))
        image = OsImage.objects.get(os_template=stale)
        self.assertEqual(image.node_template_ref, 'jammy-server-cloudimg-amd64.img')

    def test_upsert_keeps_custom_cloud_url(self):
        custom = SystemOsTemplate.objects.create(
            name='Ubuntu',
            version='24.04',
            code='ubuntu-24.04',
            backup_filename='noble-server-cloudimg-amd64.img',
            download_url='https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cloud-images/noble/current/noble-server-cloudimg-amd64.img',
            source_type=SystemOsTemplate.SourceType.CLOUD,
        )
        again = upsert_os_template_from_pop_item({
            'code': 'ubuntu-24.04',
            'name': 'Ubuntu 24.04 (Noble)',
            'url': 'https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img',
            'filename': 'noble-server-cloudimg-amd64.img',
        })
        custom.refresh_from_db()
        self.assertEqual(again.pk, custom.pk)
        self.assertIn('tuna.tsinghua', custom.download_url)

    def test_seed_defaults_upgrades_vma_and_creates_missing(self):
        SystemOsTemplate.objects.create(
            name='Ubuntu',
            version='22.04',
            backup_filename='ubuntu-22.04.vma.zst',
            download_url='http://old.example.com/ubuntu-22.04.vma.zst',
            source_type=SystemOsTemplate.SourceType.BACKUP,
        )
        response = self.client.post('/api/system/os-templates/seed_defaults/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertGreaterEqual(response.data['upgraded_count'], 1)
        self.assertGreaterEqual(response.data['created_count'], 1)
        stale = SystemOsTemplate.objects.get(name='Ubuntu', version='22.04')
        self.assertEqual(stale.source_type, SystemOsTemplate.SourceType.CLOUD)
        self.assertTrue(stale.backup_filename.endswith('.img') or stale.backup_filename.endswith('.qcow2'))
        centos7 = SystemOsTemplate.objects.get(code='centos-7')
        centos8 = SystemOsTemplate.objects.get(code='centos-8')
        self.assertIn('cloud.centos.org/centos/7/', centos7.download_url)
        self.assertTrue(centos7.backup_filename.endswith('.qcow2'))
        self.assertIn('cloud.centos.org/centos/8/', centos8.download_url)
        self.assertEqual(centos7.os_type, 'centos')
        self.assertEqual(centos8.os_type, 'centos')

    def test_pop_type_dispatches_wget_and_other_types_skip(self):
        from types import SimpleNamespace

        self.pop.pop_type = 'pe'
        self.pop.api_base_url = 'http://node.example'
        self.pop.mgmt_token = 'pem_test.secret'
        self.pop.save(update_fields=['pop_type', 'api_base_url', 'mgmt_token'])
        skipped = dispatch_pop_os_image_downloads(self.pop)
        self.assertTrue(skipped['skipped'])

        self.pop.pop_type = 'pop'
        self.pop.save(update_fields=['pop_type'])
        calls = []
        client = SimpleNamespace(
            list_pve_hosts=lambda: [{
                'id': 3,
                'default_node': 'pve',
                'storages': [{'storage': 'local', 'content': 'iso,backup', 'node': 'pve'}],
            }],
            download_template=lambda payload, timeout=60: calls.append(payload) or {'id': 'job'},
        )
        with patch('resource_manager.pve_ops._connect', return_value=(client, {'auth': 'token'})):
            result = dispatch_pop_os_image_downloads(self.pop)
        self.assertFalse(result['skipped'])
        centos = next(item for item in calls if item['filename'].startswith('CentOS-7-'))
        self.assertIn('cloud.centos.org/centos/7/', centos['url'])
        self.assertFalse(centos['via_manager'])
        self.assertFalse(centos['wait'])
        self.assertEqual(centos['content'], 'iso')
        self.assertEqual(centos['storage'], 'local')

    def test_portal_hides_stale_backup_until_upgraded(self):
        stale = SystemOsTemplate.objects.create(
            name='Ubuntu',
            version='22.04',
            backup_filename='ubuntu-22.04.vma.zst',
            download_url='http://old.example.com/ubuntu-22.04.vma.zst',
            source_type=SystemOsTemplate.SourceType.BACKUP,
        )
        product = Product.objects.create(
            name='仅旧备份 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
        )
        product.os_templates.set([stale])
        with patch('resource_manager.pve_ops.fetch_pop_os_catalog', return_value=[]):
            hidden = self.client.get(f'/api/portal/os-templates/?product_id={product.pk}')
        self.assertEqual(hidden.status_code, 200, hidden.data)
        self.assertEqual(hidden.data['data'], [])

        catalog = [{
            'code': 'ubuntu-22.04',
            'name': 'Ubuntu 22.04 (Jammy)',
            'filename': 'jammy-server-cloudimg-amd64.img',
            'url': 'http://mirror.example.com/jammy-server-cloudimg-amd64.img',
            'ready': True,
            'default_user': 'ubuntu',
        }]
        with patch('resource_manager.pve_ops.fetch_pop_os_catalog', return_value=catalog):
            shown = self.client.get(f'/api/portal/os-templates/?product_id={product.pk}')
        self.assertEqual(shown.status_code, 200, shown.data)
        self.assertEqual(len(shown.data['data']), 1)
        self.assertEqual(shown.data['data'][0]['id'], stale.pk)
        stale.refresh_from_db()
        self.assertEqual(stale.source_type, SystemOsTemplate.SourceType.CLOUD)
