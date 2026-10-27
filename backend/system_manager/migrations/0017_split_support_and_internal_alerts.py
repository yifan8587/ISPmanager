from django.db import migrations, models


SUPPORT_KEYS = (
    'support_email',
    'support_phone',
    'support_qq',
    'support_wechat',
    'support_wechat_qr',
    'support_telegram',
    'support_wecom_kf_url',
)

SUPPORT_META = {
    'support_email': ('客服邮箱', '门户对外展示的支持邮箱；对内告警请走「通知 / IM 对接」', 1),
    'support_phone': ('客服电话', '门户对外展示的支持电话；对内告警请走「通知 / IM 对接」', 2),
    'support_qq': ('客服 QQ', '客户门户在线客服展示的 QQ 号，用于添加好友或发起会话', 3),
    'support_wechat': ('客服微信号', '客户门户在线客服展示的微信号（个人/企业微信 ID）', 4),
    'support_wechat_qr': ('客服微信二维码', '微信添加好友二维码图片 URL，门户弹窗中展示', 5),
    'support_telegram': (
        '客服 Telegram',
        '门户对外展示的用户名或链接，如 @username 或 https://t.me/username。不要填写内部群 chat_id',
        6,
    ),
    'support_wecom_kf_url': (
        '企业微信客服入口',
        '门户对外展示的企微客服链接。IM 通道里的客服配置只用于发消息，不会自动出现在门户',
        7,
    ),
}


def split_support_settings(apps, schema_editor):
    SystemSetting = apps.get_model('system_manager', 'SystemSetting')
    IMChannel = apps.get_model('system_manager', 'IMChannel')

    for key in SUPPORT_KEYS:
        label, description, sort_order = SUPPORT_META[key]
        obj, _ = SystemSetting.objects.get_or_create(
            key=key,
            defaults={
                'category': 'support',
                'label': label,
                'description': description,
                'value_type': 'string',
                'default_value': '',
                'value': '',
                'is_public': True,
                'is_editable': True,
                'sort_order': sort_order,
            },
        )
        obj.category = 'support'
        obj.label = label
        obj.description = description
        obj.is_public = True
        obj.sort_order = sort_order
        obj.save(update_fields=['category', 'label', 'description', 'is_public', 'sort_order'])

    wecom = SystemSetting.objects.get(key='support_wecom_kf_url')
    if not (wecom.value or '').strip():
        channel = (
            IMChannel.objects.filter(channel_type='wecom_kf', enabled=True)
            .exclude(wecom_kf_url='')
            .order_by('-updated_at')
            .first()
        )
        if channel and channel.wecom_kf_url:
            wecom.value = channel.wecom_kf_url
            wecom.save(update_fields=['value'])


def sync_support_permissions(apps, schema_editor):
    PermissionDefinition = apps.get_model('accounts', 'PermissionDefinition')
    RolePermissionGrant = apps.get_model('accounts', 'RolePermissionGrant')
    if not PermissionDefinition.objects.exists():
        return

    defs = [
        ('system.settings.support.view', '查看对外客服', 'view', 'setting_category', 'support', ''),
        ('system.settings.support.manage', '修改对外客服', 'manage', 'setting_category', 'support', ''),
    ]
    for key, (label, _desc, _sort) in SUPPORT_META.items():
        defs.append((
            f'system.setting.{key}.view', f'查看参数「{label}」',
            'view', 'setting_key', 'support', key,
        ))
        defs.append((
            f'system.setting.{key}.manage', f'修改参数「{label}」',
            'manage', 'setting_key', 'support', key,
        ))

    for code, label, action, scope, cat, skey in defs:
        PermissionDefinition.objects.update_or_create(
            code=code,
            defaults={
                'label': label,
                'action': action,
                'scope': scope,
                'group_key': 'system',
                'page_key': 'settings',
                'setting_category': cat,
                'setting_key': skey,
                'is_builtin': True,
                'is_active': True,
            },
        )

    PermissionDefinition.objects.filter(
        scope='setting_key',
        setting_key__in=SUPPORT_KEYS,
    ).update(setting_category='support')

    for src, dest in (
        ('system.settings.general.manage', 'system.settings.support.manage'),
        ('system.settings.general.view', 'system.settings.support.view'),
    ):
        for grant in RolePermissionGrant.objects.filter(permission_id=src):
            RolePermissionGrant.objects.get_or_create(
                role=grant.role,
                permission_id=dest,
            )


def noop(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('system_manager', '0016_notificationrule_event_action_filters_and_more'),
        ('accounts', '0005_customer_role_billing_pay'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemsetting',
            name='category',
            field=models.CharField(
                choices=[
                    ('general', '基础设置'),
                    ('support', '对外客服'),
                    ('security', '安全设置'),
                    ('billing', '账单设置'),
                    ('notification', '对内通知'),
                    ('integration', '集成设置'),
                ],
                default='general',
                max_length=32,
                verbose_name='分类',
            ),
        ),
        migrations.AlterField(
            model_name='imchannel',
            name='default_target',
            field=models.CharField(
                blank=True,
                help_text='对内告警接收标识，不会展示到客户门户。Telegram=chat_id；企业微信=成员账号(touser)；企业微信客服=external_userid；钉钉=@手机号',
                max_length=256,
                verbose_name='默认接收标识',
            ),
        ),
        migrations.RunPython(split_support_settings, noop),
        migrations.RunPython(sync_support_permissions, noop),
    ]
