from django.apps import AppConfig


class SupplierManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'supplier_manager'
    verbose_name = '供应商管理'

    def ready(self):
        from . import signals  # noqa: F401
