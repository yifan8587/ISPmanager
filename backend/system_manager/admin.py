from django.contrib import admin

from .models import (
    IMChannel, IMMessageLog, NotificationRule, PaymentChannel,
    SystemBackup, SystemOsTemplate, SystemSetting, SystemUpgradePackage, WeComAIBotMessage,
)


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ('key', 'label', 'category', 'value_type', 'value', 'is_public', 'is_editable', 'updated_at')
    list_filter = ('category', 'value_type', 'is_public', 'is_editable')
    search_fields = ('key', 'label', 'description')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('category', 'sort_order', 'key')

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(SystemOsTemplate)
class SystemOsTemplateAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'version', 'os_type', 'source_type', 'backup_filename', 'enabled',
        'sort_order', 'updated_at',
    )
    list_filter = ('os_type', 'source_type', 'enabled')
    search_fields = ('name', 'version', 'code', 'backup_filename', 'download_url')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('sort_order', 'id')


@admin.register(PaymentChannel)
class PaymentChannelAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider', 'enabled', 'sandbox', 'allowed_currencies', 'sort_order', 'updated_at')
    list_filter = ('provider', 'enabled', 'sandbox')
    search_fields = ('name', 'remark')
    readonly_fields = ('encrypted_config', 'created_at', 'updated_at')
    ordering = ('sort_order', 'provider', 'id')


@admin.register(IMChannel)
class IMChannelAdmin(admin.ModelAdmin):
    list_display = ('name', 'channel_type', 'enabled', 'last_test_status', 'last_test_at', 'updated_at')
    list_filter = ('channel_type', 'enabled', 'last_test_status')
    search_fields = ('name', 'remark')
    readonly_fields = ('last_test_at', 'last_test_status', 'last_test_message', 'created_at', 'updated_at')


@admin.register(NotificationRule)
class NotificationRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'event', 'enabled', 'updated_at')
    list_filter = ('event', 'enabled')
    search_fields = ('name', 'remark')
    filter_horizontal = ('channels',)


@admin.register(IMMessageLog)
class IMMessageLogAdmin(admin.ModelAdmin):
    list_display = ('title', 'channel', 'channel_type', 'event', 'target', 'success', 'created_at')
    list_filter = ('channel_type', 'event', 'success')
    search_fields = ('title', 'content', 'target')
    readonly_fields = tuple(f.name for f in IMMessageLog._meta.fields)


@admin.register(SystemBackup)
class SystemBackupAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'backup_type', 'status', 'source_version',
        'file_size', 'created_by', 'created_at', 'finished_at',
    )
    list_filter = ('backup_type', 'status')
    search_fields = ('name', 'file_name', 'note', 'checksum_sha256')
    readonly_fields = (
        'file_name', 'storage_path', 'file_size', 'checksum_sha256',
        'created_at', 'finished_at',
    )


@admin.register(SystemUpgradePackage)
class SystemUpgradePackageAdmin(admin.ModelAdmin):
    list_display = (
        'filename', 'package_version', 'status', 'from_version', 'to_version',
        'uploaded_by', 'uploaded_at', 'finished_at',
    )
    list_filter = ('status',)
    search_fields = ('filename', 'package_version', 'changelog', 'checksum_sha256')
    readonly_fields = (
        'storage_path', 'file_size', 'checksum_sha256', 'apply_log',
        'uploaded_at', 'applied_at', 'finished_at',
    )


@admin.register(WeComAIBotMessage)
class WeComAIBotMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'channel', 'target', 'status', 'attempts', 'created_at', 'sent_at')
    list_filter = ('status', 'channel')
    search_fields = ('target', 'title', 'content', 'response')
    readonly_fields = tuple(f.name for f in WeComAIBotMessage._meta.fields)
