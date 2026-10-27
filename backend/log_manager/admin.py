from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'module', 'action_type', 'operator_name',
        'description', 'ip_address', 'source',
    )
    list_filter = ('module', 'action_type', 'source')
    search_fields = ('description', 'operator_name', 'target_repr', 'request_path')
    readonly_fields = [field.name for field in AuditLog._meta.fields]
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
