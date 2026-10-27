from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.forms.models import BaseInlineFormSet

from .models import UserProfile, APIToken, PermissionGroup, PermissionPage, PermissionDefinition, RolePermissionGrant


class UserProfileInlineFormSet(BaseInlineFormSet):
    """新建 User 时 post_save 信号会先创建档案，内联保存需 update_or_create 避免重复。"""

    def save_new(self, form, commit=True):
        obj = form.save(commit=False)
        obj.user = self.instance
        if not commit:
            return obj
        profile, _ = UserProfile.objects.update_or_create(
            user=self.instance,
            defaults={
                'account_type': obj.account_type,
                'role': obj.role,
                'phone': obj.phone,
                'department': obj.department,
                'position': obj.position,
                'avatar': obj.avatar,
                'remark': obj.remark,
            },
        )
        return profile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    formset = UserProfileInlineFormSet
    can_delete = False
    max_num = 1
    min_num = 1
    verbose_name_plural = '扩展信息'
    fields = ('account_type', 'role', 'phone', 'department', 'position', 'avatar', 'remark')
    readonly_fields = ()


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = (
        'username', 'email', 'first_name', 'last_name',
        'get_account_type', 'get_role', 'is_staff', 'is_active', 'date_joined',
    )
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'profile__role', 'profile__account_type')

    @admin.display(description='账号类型')
    def get_account_type(self, obj):
        try:
            return obj.profile.get_account_type_display()
        except UserProfile.DoesNotExist:
            return '-'

    @admin.display(description='角色')
    def get_role(self, obj):
        try:
            return obj.profile.get_role_display()
        except UserProfile.DoesNotExist:
            return '-'


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(PermissionGroup)
class PermissionGroupAdmin(admin.ModelAdmin):
    list_display = ('key', 'label', 'sort_order', 'is_builtin', 'updated_at')
    search_fields = ('key', 'label')


@admin.register(PermissionPage)
class PermissionPageAdmin(admin.ModelAdmin):
    list_display = ('group', 'key', 'label', 'route', 'is_builtin')
    list_filter = ('group',)


@admin.register(PermissionDefinition)
class PermissionDefinitionAdmin(admin.ModelAdmin):
    list_display = ('code', 'label', 'action', 'scope', 'group_key', 'is_active', 'is_builtin')
    list_filter = ('scope', 'group_key', 'is_active')
    search_fields = ('code', 'label')


@admin.register(RolePermissionGrant)
class RolePermissionGrantAdmin(admin.ModelAdmin):
    list_display = ('role', 'permission', 'created_at')
    list_filter = ('role',)


@admin.register(APIToken)
class APITokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'name', 'token_prefix', 'is_active', 'expires_at', 'last_used_at', 'created_at')
    list_filter = ('is_active', 'user')
    readonly_fields = ('token_hash', 'token_prefix', 'created_at', 'last_used_at', 'last_used_ip')
    search_fields = ('user__username', 'name', 'token_prefix')

    def has_add_permission(self, request):
        return False  # 只能通过 API 创建
