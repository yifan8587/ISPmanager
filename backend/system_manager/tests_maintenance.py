from io import BytesIO
import json
import os
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from rest_framework.test import APIClient

from backend.csrf_middleware import CompatibleCsrfViewMiddleware
from backend.csrf_origins import (
    collect_public_ports,
    expand_trusted_origins,
    hostname_of,
    iter_hosts,
    origin_for,
    origin_matches_request_host,
)
from system_manager.maintenance import (
    MaintenanceError,
    discover_overlay_roots,
    inspect_upgrade_package,
    overlay_extracted_files,
    overlay_file_allowed,
    overlay_path_skippable,
    register_upgrade_package,
    replace_overlay_file,
    start_backup,
)
from system_manager.models import SystemBackup, SystemUpgradePackage
from system_manager.version import compare_versions, format_version_display, normalize_version, parse_version


class VersionHelperTests(TestCase):
    def test_parse_and_display(self):
        self.assertEqual(parse_version('V1.1'), (1, 1))
        self.assertEqual(parse_version('1.2.0'), (1, 2, 0))
        self.assertEqual(normalize_version('v1.1'), '1.1')
        self.assertEqual(format_version_display('1.1'), 'V1.1')
        self.assertEqual(compare_versions('1.1', '1.2'), -1)
        self.assertEqual(compare_versions('V1.2', '1.1.0'), 1)
        self.assertEqual(compare_versions('1.1', '1.1.0'), 0)

    def test_overlay_blocks_secrets_and_traversal(self):
        self.assertTrue(overlay_file_allowed('backend/system_manager/views.py'))
        self.assertFalse(overlay_file_allowed('../etc/passwd'))
        self.assertFalse(overlay_file_allowed('deploy/.env'))
        self.assertFalse(overlay_file_allowed('venv/lib/x.py'))
        self.assertFalse(overlay_file_allowed('backend/media/x.bin'))
        self.assertTrue(overlay_path_skippable('ReadMe/45-变更记录.md'))
        self.assertTrue(overlay_path_skippable('README.md'))
        self.assertFalse(overlay_path_skippable('backend/models.py'))
        self.assertFalse(overlay_path_skippable('frontend/dist/index.html'))


class OverlayPermissionTests(TestCase):
    def test_replace_overwrites_readonly_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            src = root / 'src.txt'
            dest = root / 'dest.txt'
            src.write_text('new-content', encoding='utf-8')
            dest.write_text('old', encoding='utf-8')
            dest.chmod(0o444)
            replace_overlay_file(src, dest)
            self.assertEqual(dest.read_text(encoding='utf-8'), 'new-content')
            self.assertTrue(os.access(dest, os.W_OK))

    def test_overlay_replaces_readonly_readme(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            src = root / 'payload'
            dest = root / 'app'
            (src / 'ReadMe').mkdir(parents=True)
            (dest / 'ReadMe').mkdir(parents=True)
            (src / 'ReadMe' / '45.md').write_text('updated', encoding='utf-8')
            target = dest / 'ReadMe' / '45.md'
            target.write_text('stale', encoding='utf-8')
            target.chmod(0o444)
            copied = overlay_extracted_files(src, dest)
            self.assertEqual(copied, 1)
            self.assertEqual(target.read_text(encoding='utf-8'), 'updated')

    def test_overlay_skips_unwritable_docs_and_updates_code(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            src = root / 'payload'
            dest = root / 'app'
            (src / 'ReadMe').mkdir(parents=True)
            (src / 'backend').mkdir(parents=True)
            (dest / 'ReadMe').mkdir(parents=True)
            (dest / 'backend').mkdir(parents=True)
            (src / 'ReadMe' / '45.md').write_text('docs', encoding='utf-8')
            (src / 'backend' / 'ok.py').write_text('x=2\n', encoding='utf-8')
            (dest / 'backend' / 'ok.py').write_text('x=1\n', encoding='utf-8')

            def _fake_replace(src_path, target):
                if 'ReadMe' in Path(target).parts:
                    raise PermissionError(13, 'Permission denied', str(target))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(Path(src_path).read_bytes())

            with patch('system_manager.maintenance.replace_overlay_file', side_effect=_fake_replace):
                copied = overlay_extracted_files(src, dest)
            self.assertEqual(copied, 1)
            self.assertEqual((dest / 'backend' / 'ok.py').read_text(encoding='utf-8'), 'x=2\n')

    def test_overlay_permission_on_code_raises_hint(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            src = root / 'payload'
            dest = root / 'app'
            (src / 'backend').mkdir(parents=True)
            (src / 'backend' / 'ok.py').write_text('x=1\n', encoding='utf-8')
            with patch(
                'system_manager.maintenance.replace_overlay_file',
                side_effect=PermissionError(13, 'Permission denied', 'ok.py'),
            ):
                with self.assertRaises(MaintenanceError) as ctx:
                    overlay_extracted_files(src, dest)
            self.assertIn('Errno 13', str(ctx.exception))
            self.assertIn('chown', str(ctx.exception))


def _make_tar(tmp: Path, files: dict[str, bytes]) -> Path:
    archive = tmp / 'pkg.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, BytesIO(content))
    return archive


class UpgradePackageInspectTests(TestCase):
    def test_reads_version_and_rejects_downgrade(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            archive = _make_tar(tmp, {
                'VERSION': b'1.2\n',
                'manifest.json': json.dumps({
                    'name': 'ISPmanager',
                    'version': '1.2',
                    'min_version': '1.1',
                    'changelog': 'bugfix',
                }).encode(),
                'backend/demo.py': b'print(1)\n',
            })
            info = inspect_upgrade_package(archive)
            self.assertEqual(info['version'], '1.2')
            self.assertEqual(info['changelog'], 'bugfix')
            with patch('system_manager.maintenance.get_system_version', return_value={'version': '1.1'}):
                from system_manager.maintenance import validate_upgrade_against_current
                validate_upgrade_against_current(info)
            with patch('system_manager.maintenance.get_system_version', return_value={'version': '1.3'}):
                from system_manager.maintenance import validate_upgrade_against_current
                with self.assertRaises(MaintenanceError):
                    validate_upgrade_against_current(info)

    def test_ignores_deploy_version_metadata(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            archive = _make_tar(tmp, {
                'ISPmanager/VERSION': b'1.1.1\n',
                'ISPmanager/deploy/VERSION': b'name=ispmanager-ispweb\nrelease=ispmanager-release-20260825-160000\n',
                'ISPmanager/manifest.json': json.dumps({'version': '1.1.1'}).encode(),
                'ISPmanager/backend/ok.py': b'x=1\n',
                'ISPweb/dist/index.html': b'<html></html>\n',
            })
            info = inspect_upgrade_package(archive)
            self.assertEqual(info['version'], '1.1.1')

    def test_discover_overlay_covers_portal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            extract = root / 'pkg'
            dest = root / 'app' / 'ISPmanager'
            (extract / 'ISPmanager' / 'backend').mkdir(parents=True)
            (extract / 'ISPweb' / 'dist').mkdir(parents=True)
            (extract / 'ISPmanager' / 'VERSION').write_text('1.1.1\n', encoding='utf-8')
            pairs = discover_overlay_roots(extract, dest)
            self.assertEqual(len(pairs), 2)
            self.assertEqual(pairs[0][0].name, 'ISPmanager')
            self.assertEqual(pairs[1][0].name, 'ISPweb')
            self.assertEqual(pairs[1][1], dest.parent / 'ISPweb')


class CsrfOriginHelperTests(TestCase):
    def test_expand_includes_https_and_aliases(self):
        hosts = iter_hosts('www.example.net', 'portal.example.net 10.0.0.8', ['*'])
        self.assertIn('www.example.net', hosts)
        self.assertIn('portal.example.net', hosts)
        self.assertIn('10.0.0.8', hosts)
        self.assertNotIn('*', hosts)
        origins = expand_trusted_origins(explicit=[], hosts=hosts, public_port='443')
        self.assertIn('https://www.example.net', origins)
        self.assertIn('http://www.example.net', origins)
        self.assertEqual(origin_for('https', 'a.com', '443'), 'https://a.com')
        self.assertEqual(origin_for('https', 'a.com', '8443'), 'https://a.com:8443')

    def test_expand_includes_mapped_public_ports(self):
        self.assertEqual(collect_public_ports('443', '30090', '30090'), ['443', '30090'])
        origins = expand_trusted_origins(
            hosts=['www.10g.hk'],
            public_ports=['30090'],
        )
        self.assertIn('http://www.10g.hk:30090', origins)
        self.assertIn('https://www.10g.hk:30090', origins)
        self.assertIn('http://www.10g.hk', origins)
        self.assertEqual(hostname_of('http://www.10g.hk:30090'), 'www.10g.hk')
        self.assertEqual(hostname_of('www.10g.hk:30090'), 'www.10g.hk')


class CsrfMappedPortTests(TestCase):
    def _request(self, **headers):
        factory = RequestFactory()
        return factory.post('/admin/login/', **headers)

    @override_settings(
        ALLOWED_HOSTS=['www.10g.hk'],
        USE_X_FORWARDED_HOST=True,
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        CSRF_TRUSTED_ORIGINS=['https://www.10g.hk', 'http://www.10g.hk'],
    )
    def test_accepts_http_origin_with_mapped_port(self):
        request = self._request(
            HTTP_HOST='www.10g.hk',
            HTTP_X_FORWARDED_HOST='www.10g.hk',
            HTTP_X_FORWARDED_PROTO='http',
            HTTP_ORIGIN='http://www.10g.hk:30090',
        )
        mw = CompatibleCsrfViewMiddleware(lambda _r: HttpResponse())
        self.assertTrue(origin_matches_request_host('http://www.10g.hk:30090', request))
        self.assertTrue(mw._origin_verified(request))

    @override_settings(
        ALLOWED_HOSTS=['www.10g.hk'],
        USE_X_FORWARDED_HOST=True,
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        CSRF_TRUSTED_ORIGINS=['https://www.10g.hk', 'http://www.10g.hk'],
    )
    def test_accepts_when_proxy_marks_https_but_browser_uses_http_port(self):
        request = self._request(
            HTTP_HOST='www.10g.hk',
            HTTP_X_FORWARDED_HOST='www.10g.hk',
            HTTP_X_FORWARDED_PROTO='https',
            HTTP_ORIGIN='http://www.10g.hk:30090',
        )
        mw = CompatibleCsrfViewMiddleware(lambda _r: HttpResponse())
        self.assertTrue(mw._origin_verified(request))

    @override_settings(
        ALLOWED_HOSTS=['www.10g.hk'],
        CSRF_TRUSTED_ORIGINS=['https://www.10g.hk', 'http://www.10g.hk'],
    )
    def test_rejects_foreign_origin(self):
        request = self._request(
            HTTP_HOST='www.10g.hk',
            HTTP_ORIGIN='http://evil.example',
        )
        mw = CompatibleCsrfViewMiddleware(lambda _r: HttpResponse())
        self.assertFalse(mw._origin_verified(request))

    @override_settings(ALLOWED_HOSTS=['www.10g.hk', 'testserver'])
    def test_admin_login_post_from_mapped_port_not_403(self):
        User.objects.create_superuser('csrf-admin', 'csrf@example.test', 'SafePass_2026!')
        client = Client(enforce_csrf_checks=True)
        login_page = client.get('/admin/login/', HTTP_HOST='www.10g.hk')
        self.assertEqual(login_page.status_code, 200)
        token = login_page.context['csrf_token']
        resp = client.post(
            '/admin/login/',
            {
                'username': 'csrf-admin',
                'password': 'SafePass_2026!',
                'next': '/admin/',
                'csrfmiddlewaretoken': str(token),
            },
            HTTP_HOST='www.10g.hk',
            HTTP_ORIGIN='http://www.10g.hk:30090',
            HTTP_REFERER='http://www.10g.hk:30090/admin/login/',
        )
        self.assertNotEqual(resp.status_code, 403, resp.content[:400])
        self.assertIn(resp.status_code, (200, 302))


@override_settings(SYSTEM_MAINTENANCE_SYNC=True, SYSTEM_UPGRADE_RESTART=False)
class MaintenanceAPITests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.backup_dir = root / 'backups'
        self.upgrade_dir = root / 'upgrades'
        self.version_file = root / 'VERSION'
        self.version_file.write_text('1.1\n', encoding='utf-8')
        self.override = override_settings(
            SYSTEM_BACKUP_DIR=str(self.backup_dir),
            SYSTEM_UPGRADE_DIR=str(self.upgrade_dir),
            ISPMANAGER_VERSION_FILE=str(self.version_file),
            SYSTEM_MAINTENANCE_SYNC=True,
            SYSTEM_UPGRADE_RESTART=False,
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = User.objects.create_superuser('maint-admin', 'm@example.test', 'SafePass_2026!')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_version_endpoint(self):
        resp = self.client.get('/api/system/version/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['version'], '1.1')
        self.assertEqual(resp.data['display'], 'V1.1')

    def test_overview_includes_version(self):
        resp = self.client.get('/api/system/overview/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['version_display'], 'V1.1')

    @patch('system_manager.maintenance.export_database')
    def test_create_backup(self, export_db):
        def _export(dest):
            (dest / 'data.json').write_text('[]', encoding='utf-8')
            return {
                'engine': 'django',
                'payload': 'data.json',
                'payload_format': 'django_dumpdata',
                'database': 'test',
            }
        export_db.side_effect = _export
        resp = self.client.post('/api/system/backups/', {'note': '日常备份'}, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        backup = SystemBackup.objects.get(pk=resp.data['id'])
        self.assertEqual(backup.status, SystemBackup.Status.SUCCESS)
        self.assertTrue(Path(backup.storage_path).is_file())
        self.assertGreater(backup.file_size, 0)

        listed = self.client.get('/api/system/backups/')
        self.assertEqual(listed.status_code, 200)
        rows = listed.data.get('results', listed.data)
        self.assertEqual(len(rows), 1)

        down = self.client.get(f'/api/system/backups/{backup.pk}/download/')
        self.assertEqual(down.status_code, 200)
        self.assertTrue(down.get('Content-Disposition', '').startswith('attachment'))

    def test_restore_requires_confirm(self):
        backup = SystemBackup.objects.create(
            name='dummy',
            status=SystemBackup.Status.SUCCESS,
            file_name='x.tar.gz',
            storage_path='/tmp/not-exist.tar.gz',
        )
        resp = self.client.post(f'/api/system/backups/{backup.pk}/restore/', {}, format='json')
        self.assertEqual(resp.status_code, 400)

    @patch('system_manager.maintenance.get_system_version', return_value={
        'version': '1.1', 'display': 'V1.1', 'product': 'ISPmanager', 'source': 'file',
        'version_file': '',
    })
    def test_upload_upgrade_package(self, _ver):
        archive = _make_tar(Path(self.tmp.name), {
            'VERSION': b'1.2\n',
            'manifest.json': json.dumps({
                'version': '1.2',
                'min_version': '1.1',
                'changelog': '功能更新',
            }).encode(),
            'backend/ok.py': b'x=1\n',
        })
        with archive.open('rb') as fh:
            uploaded = SimpleUploadedFile('ispmanager-update-1.2.tar.gz', fh.read(), content_type='application/gzip')
        resp = self.client.post('/api/system/upgrades/upload/', {'file': uploaded}, format='multipart')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['package_version'], '1.2')
        self.assertEqual(resp.data['status'], SystemUpgradePackage.Status.READY)

    def test_upload_same_version_rejected(self):
        archive = _make_tar(Path(self.tmp.name), {
            'VERSION': b'1.1\n',
            'manifest.json': json.dumps({'version': '1.1'}).encode(),
        })
        with archive.open('rb') as fh:
            uploaded = SimpleUploadedFile('same.tar.gz', fh.read(), content_type='application/gzip')
        resp = self.client.post('/api/system/upgrades/upload/', {'file': uploaded}, format='multipart')
        self.assertEqual(resp.status_code, 400, resp.data)

    @patch('system_manager.maintenance.export_database')
    @patch('system_manager.maintenance.apply_code_upgrade')
    @patch('system_manager.maintenance.overlay_extracted_files', return_value=2)
    @patch('system_manager.maintenance.call_command')
    def test_apply_upgrade(self, _cmd, _overlay, _apply_code, export_db):
        def _export(dest):
            (dest / 'data.json').write_text('[]', encoding='utf-8')
            return {
                'engine': 'django',
                'payload': 'data.json',
                'payload_format': 'django_dumpdata',
                'database': 'test',
            }
        export_db.side_effect = _export
        archive = _make_tar(Path(self.tmp.name), {
            'VERSION': b'1.2\n',
            'manifest.json': json.dumps({
                'version': '1.2',
                'changelog': '升级测试',
            }).encode(),
            'backend/ok.py': b'x=1\n',
        })
        with archive.open('rb') as fh:
            uploaded = SimpleUploadedFile('up.tar.gz', fh.read(), content_type='application/gzip')
        created = register_upgrade_package(uploaded=uploaded, user=self.user)
        resp = self.client.post(
            f'/api/system/upgrades/{created.pk}/apply/',
            {'confirm': 'UPGRADE'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        created.refresh_from_db()
        self.assertEqual(created.status, SystemUpgradePackage.Status.SUCCESS)
        self.assertEqual(self.version_file.read_text(encoding='utf-8').strip(), '1.2')
