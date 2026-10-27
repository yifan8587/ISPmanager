from django.apps import AppConfig


class LogManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'log_manager'
    verbose_name = '日志管理'

    def ready(self):
        from . import signals
        signals.connect_audit_signals()
