"""
accounts/signals.py
新建 User 时自动创建 UserProfile；Customer 关联变更时同步账号类型
"""
from django.db.models.signals import post_save
from django.contrib.auth.models import User
from django.dispatch import receiver

from .models import UserProfile


def _default_role(user: User) -> str:
    if user.is_superuser:
        return UserProfile.Role.SUPERADMIN
    if user.is_staff:
        return UserProfile.Role.ADMIN
    if hasattr(user, 'customer') and user.customer is not None:
        return UserProfile.Role.CUSTOMER_USER
    return UserProfile.Role.OPERATOR


def _default_account_type(user: User) -> str:
    if hasattr(user, 'customer') and user.customer is not None:
        return UserProfile.AccountType.CUSTOMER
    return UserProfile.AccountType.SYSTEM


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(
            user=instance,
            defaults={
                'role': _default_role(instance),
                'account_type': _default_account_type(instance),
            },
        )
    else:
        # 已有档案时，Customer 关联可能在 User 创建后才绑定
        try:
            profile = instance.profile
        except UserProfile.DoesNotExist:
            return
        profile.sync_account_type_from_customer_link(save=True)


def sync_profile_account_type(user: User):
    """供 customer_manager 在绑定/解绑 Customer.account 后调用。"""
    from .role_permissions import CUSTOMER_ACCOUNT_ALLOWED_ROLES, CUSTOMER_ACCOUNT_DEFAULT_ROLE

    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'role': _default_role(user),
                'account_type': _default_account_type(user),
            },
        )
        return

    type_changed = profile.sync_account_type_from_customer_link(save=False)
    update_fields = []
    if type_changed:
        update_fields.append('account_type')

    # 归属客户账号角色必须落在允许集合内，默认「客户用户」
    if profile.account_type == UserProfile.AccountType.CUSTOMER:
        if profile.role not in CUSTOMER_ACCOUNT_ALLOWED_ROLES:
            profile.role = CUSTOMER_ACCOUNT_DEFAULT_ROLE
            update_fields.append('role')

    if update_fields:
        update_fields.append('updated_at')
        profile.save(update_fields=update_fields)


def _connect_customer_signals():
    """Customer.account 变更时同步 UserProfile.account_type。"""
    try:
        from customer_manager.models import Customer
    except Exception:  # noqa: BLE001
        return

    @receiver(post_save, sender=Customer, dispatch_uid='accounts_sync_customer_account_type')
    def on_customer_account_changed(sender, instance, **kwargs):
        if instance.account_id:
            sync_profile_account_type(instance.account)


_connect_customer_signals()
