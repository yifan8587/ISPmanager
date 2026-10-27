"""S-H6：生产启动拒绝不安全默认配置。"""
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from backend.security_runtime import (
    DEFAULT_DB_PASSWORD,
    INSECURE_DEFAULT_SECRET,
    collect_insecure_runtime_errors,
    enforce_secure_runtime,
)


class SecurityRuntimeTests(SimpleTestCase):
    def test_dev_debug_skips_checks(self):
        self.assertEqual(collect_insecure_runtime_errors(
            debug=True,
            secret_key=INSECURE_DEFAULT_SECRET,
            allowed_hosts=['*'],
            cors_allow_all=True,
            db_password=DEFAULT_DB_PASSWORD,
            production=False,
        ), [])

    def test_production_rejects_defaults(self):
        errors = collect_insecure_runtime_errors(
            debug=True,
            secret_key=INSECURE_DEFAULT_SECRET,
            allowed_hosts=['*'],
            cors_allow_all=True,
            db_password=DEFAULT_DB_PASSWORD,
            production=True,
        )
        self.assertGreaterEqual(len(errors), 4)

    def test_debug_false_requires_secret_and_hosts(self):
        errors = collect_insecure_runtime_errors(
            debug=False,
            secret_key='a-real-production-secret-key-32chars',
            allowed_hosts=['admin.example.com'],
            cors_allow_all=False,
            db_password=DEFAULT_DB_PASSWORD,
            production=False,
        )
        self.assertEqual(errors, [])

    def test_enforce_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            enforce_secure_runtime(
                debug=False,
                secret_key=INSECURE_DEFAULT_SECRET,
                allowed_hosts=['*'],
                cors_allow_all=True,
                db_password='ok',
                production=False,
            )
