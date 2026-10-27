from django.apps import AppConfig


class SystemManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'system_manager'
    verbose_name = '系统设置'

    def ready(self):
        import system_manager.signals  # noqa: F401
        system_manager.signals.connect_change_signals()
