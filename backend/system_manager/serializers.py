import urllib.parse

from rest_framework import serializers

from .models import (
    IMChannel, IMChannelType, IMMessageLog, NotificationEvent, NotificationFormatMode,
    NotificationRule,
    PaymentChannel,
    SystemBackup,
    SystemOsTemplate,
    SystemSetting,
    SystemUpgradePackage,
)
from .os_templates import (
    filename_from_url,
    infer_os_type,
    is_cloud_image_filename,
    is_stale_os_template,
    is_vzdump_filename,
)
from .maintenance import format_size
from .default_settings import SECRET_SETTING_KEYS
from .notification_templates import EVENT_CATALOG, validate_template as validate_notification_template
from .payment_providers import (
    PAYMENT_PROVIDER_SPECS,
    provider_fields,
    public_config_view,
    secret_config_keys,
    validate_provider_config,
)


class SystemSettingSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source='get_category_display', read_only=True)
    value_type_label = serializers.CharField(source='get_value_type_display', read_only=True)
    typed_value = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = SystemSetting
        fields = (
            'id', 'key', 'category', 'category_label',
            'label', 'description',
            'value_type', 'value_type_label',
            'value', 'default_value', 'typed_value', 'options',
            'sort_order', 'is_public', 'is_editable',
            'updated_by', 'updated_by_name',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'key', 'created_at', 'updated_at')

    def get_typed_value(self, obj):
        if obj.key in SECRET_SETTING_KEYS:
            return ''
        try:
            return obj.get_typed_value()
        except Exception:  # noqa: BLE001
            return obj.get_raw_value()

    def get_updated_by_name(self, obj):
        if not obj.updated_by:
            return None
        return obj.updated_by.get_full_name() or obj.updated_by.username

    def to_representation(self, instance):
        data = super().to_representation(instance)
        secret = instance.key in SECRET_SETTING_KEYS
        data['secret'] = secret
        data['configured'] = bool((instance.value or '').strip()) if secret else True
        if secret:
            data['value'] = ''
            data['typed_value'] = ''
            data['default_value'] = ''
        return data


class SystemSettingWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemSetting
        fields = ('value', 'label', 'description', 'sort_order', 'is_public')

    def validate(self, attrs):
        instance = self.instance
        if (
            instance
            and instance.key in SECRET_SETTING_KEYS
            and 'value' in attrs
            and not str(attrs.get('value') or '').strip()
        ):
            attrs.pop('value')
        value = attrs.get('value', getattr(instance, 'value', ''))
        value_type = getattr(instance, 'value_type', SystemSetting.ValueType.STRING)
        probe = SystemSetting(value=value, value_type=value_type, default_value=instance.default_value if instance else '')
        try:
            probe.get_typed_value()
        except Exception as exc:  # noqa: BLE001
            raise serializers.ValidationError({'value': f'配置值无效：{exc}'})
        options = getattr(instance, 'options', None) if instance else None
        if options and getattr(instance, 'key', '') == 'default_currency':
            allowed = {
                str(item.get('value')).upper()
                for item in options
                if isinstance(item, dict) and item.get('value')
            }
            if str(value or '').upper() not in allowed:
                raise serializers.ValidationError({'value': '请选择有效的默认结算币种'})
        return attrs


class SystemSettingBulkItemSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=64)
    value = serializers.CharField(allow_blank=True)


class SystemSettingBulkUpdateSerializer(serializers.Serializer):
    items = SystemSettingBulkItemSerializer(many=True)

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError('items 不能为空')
        keys = [i['key'] for i in items]
        existing = set(SystemSetting.objects.filter(key__in=keys).values_list('key', flat=True))
        missing = set(keys) - existing
        if missing:
            raise serializers.ValidationError(f'未知配置键：{", ".join(sorted(missing))}')
        return items


class PaymentChannelSerializer(serializers.ModelSerializer):
    """支付渠道管理序列化器，写入凭据但永不回显敏感字段。"""

    provider_label = serializers.CharField(source='get_provider_display', read_only=True)
    config = serializers.DictField(required=False, write_only=True)
    config_view = serializers.SerializerMethodField()
    config_schema = serializers.SerializerMethodField()
    configured = serializers.SerializerMethodField()
    callback_url = serializers.SerializerMethodField()
    callback_usage = serializers.SerializerMethodField()

    class Meta:
        model = PaymentChannel
        fields = (
            'id', 'provider', 'provider_label', 'name', 'enabled', 'sandbox',
            'allowed_currencies', 'settlement_currency', 'sort_order', 'remark',
            'config', 'config_view', 'config_schema', 'configured',
            'callback_url', 'callback_usage',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'provider_label', 'config_view', 'config_schema',
                            'configured', 'callback_url', 'callback_usage',
                            'created_at', 'updated_at')

    def get_config_view(self, obj):
        return obj.config_view()

    def get_config_schema(self, obj):
        return provider_fields(obj.provider)

    def get_configured(self, obj):
        try:
            validate_provider_config(obj.provider, obj.get_config(), enabled=True)
        except ValueError:
            return False
        return True

    def get_callback_url(self, obj):
        from .payment_urls import payment_callback_url
        return payment_callback_url(obj.id, self.context.get('request'))

    def get_callback_usage(self, obj):
        return str(PAYMENT_PROVIDER_SPECS.get(obj.provider, {}).get('callback_usage') or '')

    def validate_allowed_currencies(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('允许币种必须为列表')
        values = []
        for item in value:
            currency = str(item or '').strip().upper()
            if not currency:
                continue
            if not 3 <= len(currency) <= 8:
                raise serializers.ValidationError(f'无效币种：{currency}')
            if currency not in values:
                values.append(currency)
        return values

    def validate_settlement_currency(self, value):
        currency = str(value or '').strip().upper()
        if currency and not 3 <= len(currency) <= 8:
            raise serializers.ValidationError('无效的渠道收款币种')
        return currency

    def validate(self, attrs):
        instance = self.instance
        provider = attrs.get('provider', getattr(instance, 'provider', ''))
        if provider not in PAYMENT_PROVIDER_SPECS:
            raise serializers.ValidationError({'provider': '不支持的支付渠道'})
        if (
            instance
            and provider != instance.provider
            and instance.payment_transactions.exists()
        ):
            raise serializers.ValidationError({'provider': '已有支付交易记录的渠道不能更换服务商'})

        if instance and provider == instance.provider:
            config = instance.get_config()
        else:
            config = {}
        incoming = attrs.get('config')
        if incoming is not None:
            fields = {field['key'] for field in provider_fields(provider)}
            invalid = sorted(set(incoming) - fields)
            if invalid:
                raise serializers.ValidationError({'config': f'未知配置项：{", ".join(invalid)}'})
            secret_keys = secret_config_keys(provider)
            for key, value in incoming.items():
                text = '' if value is None else str(value)
                # 编辑时留空敏感字段代表“不修改”，避免前端回显与误清空。
                if key in secret_keys and not text.strip() and instance and provider == instance.provider:
                    continue
                config[key] = text.strip()

        enabled = attrs.get('enabled', getattr(instance, 'enabled', False))
        settlement_currency = attrs.get(
            'settlement_currency',
            getattr(instance, 'settlement_currency', ''),
        )
        allowed_currencies = attrs.get(
            'allowed_currencies',
            getattr(instance, 'allowed_currencies', []),
        )
        if settlement_currency and allowed_currencies and settlement_currency not in allowed_currencies:
            raise serializers.ValidationError({
                'settlement_currency': '渠道收款币种必须包含在允许币种中',
            })
        if enabled and allowed_currencies and not settlement_currency:
            if len(allowed_currencies) == 1:
                attrs['settlement_currency'] = allowed_currencies[0]
            else:
                raise serializers.ValidationError({
                    'settlement_currency': '启用多币种渠道时必须选择一个渠道收款币种',
                })
        try:
            validate_provider_config(provider, config, enabled=enabled)
        except ValueError as exc:
            raise serializers.ValidationError({'config': str(exc)}) from exc
        attrs['_merged_payment_config'] = config
        return attrs

    def create(self, validated_data):
        config = validated_data.pop('_merged_payment_config', {})
        validated_data.pop('config', None)
        channel = PaymentChannel(**validated_data)
        channel.set_config(config)
        channel.save()
        return channel

    def update(self, instance, validated_data):
        config = validated_data.pop('_merged_payment_config', instance.get_config())
        validated_data.pop('config', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.set_config(config)
        instance.save()
        return instance


class PaymentChannelPublicSerializer(serializers.ModelSerializer):
    """账单支付页面使用的安全通道信息。"""

    provider_label = serializers.CharField(source='get_provider_display', read_only=True)
    checkout_mode = serializers.SerializerMethodField()
    payment_currency = serializers.SerializerMethodField()
    exchange_rate = serializers.SerializerMethodField()
    converted_outstanding = serializers.SerializerMethodField()

    class Meta:
        model = PaymentChannel
        fields = (
            'id', 'provider', 'provider_label', 'name', 'sandbox',
            'allowed_currencies', 'settlement_currency', 'checkout_mode',
            'payment_currency', 'exchange_rate', 'converted_outstanding',
        )

    def get_checkout_mode(self, obj):
        return PAYMENT_PROVIDER_SPECS[obj.provider]['checkout_mode']

    def _quote(self, obj):
        billing = self.context.get('billing')
        if not billing:
            return None
        cache = getattr(self, '_payment_quote_cache', {})
        if obj.pk in cache:
            return cache[obj.pk]
        from customer_manager.payment_service import quote_channel_amount
        quote = quote_channel_amount(
            billing=billing,
            channel=obj,
            billing_amount=billing.outstanding_amount,
        )
        cache[obj.pk] = quote
        self._payment_quote_cache = cache
        return quote

    def get_payment_currency(self, obj):
        quote = self._quote(obj)
        return quote['payment_currency'] if quote else ''

    def get_exchange_rate(self, obj):
        quote = self._quote(obj)
        return str(quote['exchange_rate']) if quote else ''

    def get_converted_outstanding(self, obj):
        quote = self._quote(obj)
        return str(quote['payment_amount']) if quote else ''


# ═══════════════════════════════════════════════════════════════
# § IM 通知对接
# ═══════════════════════════════════════════════════════════════

class IMChannelSerializer(serializers.ModelSerializer):
    channel_type_label = serializers.CharField(source='get_channel_type_display', read_only=True)
    last_test_status_label = serializers.CharField(source='get_last_test_status_display', read_only=True)
    recipient_count = serializers.SerializerMethodField()
    # 敏感字段写入用，读取时不回显明文
    bot_token = serializers.CharField(required=False, allow_blank=True)
    corp_secret = serializers.CharField(required=False, allow_blank=True)
    wecom_aibot_secret = serializers.CharField(required=False, allow_blank=True)
    secret = serializers.CharField(required=False, allow_blank=True)
    webhook_url = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = IMChannel
        fields = (
            'id', 'name', 'channel_type', 'channel_type_label', 'enabled',
            'webhook_url', 'secret', 'bot_token',
            'corp_id', 'agent_id', 'corp_secret', 'wecom_secret_env',
            'wecom_kf_id', 'wecom_kf_url',
            'wecom_aibot_id', 'wecom_aibot_secret',
            'default_target', 'recipients', 'recipient_count',
            'last_test_at', 'last_test_status', 'last_test_status_label', 'last_test_message',
            'remark', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'last_test_at', 'last_test_status',
                            'last_test_message', 'created_at', 'updated_at')
        extra_kwargs = {
            'bot_token': {'write_only': True},
            'corp_secret': {'write_only': True},
            'wecom_aibot_secret': {'write_only': True},
            'secret': {'write_only': True},
        }

    def get_recipient_count(self, obj):
        return len(obj.target_list())

    def _keep_secret(self, validated_data):
        """空字符串不覆盖已有密钥。"""
        for f in ('bot_token', 'corp_secret', 'wecom_aibot_secret', 'secret'):
            if validated_data.get(f, None) == '':
                validated_data.pop(f, None)

    def update(self, instance, validated_data):
        self._keep_secret(validated_data)
        return super().update(instance, validated_data)

    def validate_recipients(self, value):
        if value in (None, ''):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('接口人需为列表')
        cleaned = []
        for item in value:
            if isinstance(item, dict) and item.get('target'):
                cleaned.append({'name': str(item.get('name', '')), 'target': str(item['target'])})
        return cleaned

    def validate(self, attrs):
        channel_type = attrs.get('channel_type', getattr(self.instance, 'channel_type', None))
        corp_id = attrs.get('corp_id') or getattr(self.instance, 'corp_id', '')
        corp_secret = attrs.get('corp_secret') or getattr(self.instance, 'corp_secret', '')
        kf_id = attrs.get('wecom_kf_id') or getattr(self.instance, 'wecom_kf_id', '')
        agent_id = attrs.get('agent_id') or getattr(self.instance, 'agent_id', '')
        aibot_id = attrs.get('wecom_aibot_id') or getattr(self.instance, 'wecom_aibot_id', '')
        aibot_secret = attrs.get('wecom_aibot_secret') or getattr(self.instance, 'wecom_aibot_secret', '')
        webhook_url = attrs.get('webhook_url', getattr(self.instance, 'webhook_url', ''))
        if channel_type == IMChannelType.WECOM:
            missing = [label for label, value in (
                ('CorpID', corp_id), ('AgentID', agent_id), ('应用 Secret', corp_secret),
                ('接收员工 UserID', attrs.get('default_target', getattr(self.instance, 'default_target', ''))),
            ) if not value]
            if missing:
                raise serializers.ValidationError({'agent_id': f'企业微信自建应用缺少：{"、".join(missing)}'})
            if not str(agent_id).isdigit():
                raise serializers.ValidationError({'agent_id': 'AgentID 必须为数字'})
        if channel_type == IMChannelType.WECOM_WEBHOOK:
            try:
                parsed = urllib.parse.urlsplit(webhook_url)
                valid_webhook = (
                    parsed.scheme == 'https'
                    and parsed.hostname == 'qyapi.weixin.qq.com'
                    and parsed.path.rstrip('/') == '/cgi-bin/webhook/send'
                    and bool(urllib.parse.parse_qs(parsed.query).get('key'))
                )
            except ValueError:
                valid_webhook = False
            if not valid_webhook:
                raise serializers.ValidationError({
                    'webhook_url': (
                        '请填写企业微信群机器人配置页生成的 Webhook 地址：'
                        'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...'
                    ),
                })
        if channel_type == IMChannelType.WECOM_KF:
            missing = [
                label for label, value in (
                    ('CorpID', corp_id),
                    ('应用 Secret', corp_secret),
                    ('客服账号 OpenKfId', kf_id),
                ) if not value
            ]
            if missing:
                raise serializers.ValidationError({
                    'wecom_kf_id': f'企业微信客服通道缺少：{"、".join(missing)}',
                })
        if channel_type == IMChannelType.WECOM_AIBOT:
            missing = [
                label for label, value in (
                    ('智能机器人 BotID', aibot_id),
                    ('长连接 Secret', aibot_secret),
                    ('接收成员 UserID', attrs.get('default_target', getattr(self.instance, 'default_target', ''))),
                ) if not value
            ]
            if missing:
                raise serializers.ValidationError({
                    'wecom_aibot_id': f'企业微信智能机器人通道缺少：{"、".join(missing)}',
                })
        return attrs


class NotificationRuleSerializer(serializers.ModelSerializer):
    # ``event`` 继续接受旧客户端的单事件写入；新客户端使用 ``events`` 多选。
    event = serializers.ChoiceField(choices=NotificationEvent.choices, required=False)
    events = serializers.ListField(
        child=serializers.ChoiceField(choices=NotificationEvent.choices),
        required=False,
        allow_empty=False,
    )
    event_label = serializers.CharField(source='get_event_display', read_only=True)
    event_labels = serializers.SerializerMethodField()
    format_mode_label = serializers.CharField(source='get_format_mode_display', read_only=True)
    channel_names = serializers.SerializerMethodField()

    class Meta:
        model = NotificationRule
        fields = (
            'id', 'name', 'event', 'event_label', 'enabled',
            'events', 'event_labels',
            'channels', 'channel_names',
            'action_filters', 'event_action_filters', 'format_mode', 'format_mode_label',
            'title_template', 'template', 'dedupe_seconds', 'remark',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')

    def get_channel_names(self, obj):
        return [{'id': c.id, 'name': c.name, 'channel_type': c.channel_type} for c in obj.channels.all()]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        events = instance.event_values()
        data['events'] = events
        event_action_filters = (
            instance.event_action_filters
            if isinstance(instance.event_action_filters, dict) else {}
        )
        if not event_action_filters and instance.action_filters:
            event_action_filters = {instance.event: instance.action_filters}
        data['event_action_filters'] = event_action_filters
        return data

    def get_event_labels(self, obj):
        labels = dict(NotificationEvent.choices)
        return [
            {'value': event, 'label': labels.get(event, event)}
            for event in obj.event_values()
        ]

    def validate_events(self, value):
        events = []
        for event in value:
            if event not in events:
                events.append(event)
        if len(events) > 20:
            raise serializers.ValidationError('每条规则最多可选择 20 个触发事件')
        return events

    def validate_action_filters(self, value):
        if value in (None, ''):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('动作筛选必须是数组')
        result = []
        for action in value:
            action = str(action).strip()
            if action and action not in result:
                result.append(action)
        if len(result) > 30:
            raise serializers.ValidationError('动作筛选最多可选择 30 项')
        return result

    def validate_dedupe_seconds(self, value):
        if value > 86400:
            raise serializers.ValidationError('重复抑制窗口不能超过 86400 秒')
        return value

    def validate(self, attrs):
        selected_events = attrs.get('events')
        legacy_event = attrs.get('event')
        if selected_events is None:
            if legacy_event:
                selected_events = [legacy_event]
            elif self.instance:
                selected_events = self.instance.event_values()
            else:
                raise serializers.ValidationError({'events': '请至少选择一个触发事件'})
        if not selected_events:
            raise serializers.ValidationError({'events': '请至少选择一个触发事件'})
        attrs['events'] = selected_events
        attrs['event'] = selected_events[0]

        action_filters = attrs.get(
            'action_filters',
            getattr(self.instance, 'action_filters', []) if self.instance else [],
        ) or []
        raw_event_action_filters = attrs.get(
            'event_action_filters',
            getattr(self.instance, 'event_action_filters', {}) if self.instance else {},
        ) or {}
        if not isinstance(raw_event_action_filters, dict):
            raise serializers.ValidationError({'event_action_filters': '按事件动作筛选必须是对象'})
        unknown_filter_events = set(raw_event_action_filters) - set(selected_events)
        if unknown_filter_events:
            raise serializers.ValidationError({
                'event_action_filters': (
                    f'动作筛选包含未选择的事件：{"、".join(sorted(unknown_filter_events))}'
                ),
            })
        event_action_filters = {}
        for event in selected_events:
            values = raw_event_action_filters.get(event)
            # 兼容旧单事件规则及旧客户端请求。
            if values is None:
                values = action_filters if event == selected_events[0] else []
            if not isinstance(values, list):
                raise serializers.ValidationError({
                    'event_action_filters': f'事件“{event}”的动作筛选必须是数组',
                })
            cleaned = []
            for action in values:
                action = str(action).strip()
                if action and action not in cleaned:
                    cleaned.append(action)
            if len(cleaned) > 30:
                raise serializers.ValidationError({
                    'event_action_filters': f'事件“{event}”最多可选择 30 个动作',
                })
            known_actions = {
                item['value'] for item in EVENT_CATALOG.get(event, {}).get('actions', [])
            }
            unknown = [action for action in cleaned if known_actions and action not in known_actions]
            if unknown:
                raise serializers.ValidationError({
                    'event_action_filters': (
                        f'事件“{event}”不支持动作：{"、".join(unknown)}'
                    ),
                })
            event_action_filters[event] = cleaned
        attrs['event_action_filters'] = event_action_filters
        # 保持旧客户端读取 action_filters 时仍能看到主事件的筛选值。
        attrs['action_filters'] = event_action_filters.get(selected_events[0], [])

        title_template = attrs.get(
            'title_template',
            getattr(self.instance, 'title_template', '') if self.instance else '',
        )
        content_template = attrs.get(
            'template',
            getattr(self.instance, 'template', '') if self.instance else '',
        )
        format_mode = attrs.get(
            'format_mode',
            getattr(self.instance, 'format_mode', NotificationFormatMode.DETAILED)
            if self.instance else NotificationFormatMode.DETAILED,
        )
        try:
            validate_notification_template(title_template, field_label='标题模板')
            validate_notification_template(content_template, field_label='正文模板')
        except ValueError as exc:
            raise serializers.ValidationError({'template': str(exc)}) from exc
        if format_mode == NotificationFormatMode.CUSTOM and not content_template:
            raise serializers.ValidationError({'template': '自定义格式必须填写正文模板'})
        return attrs


class IMMessageLogSerializer(serializers.ModelSerializer):
    channel_name = serializers.CharField(source='channel.name', read_only=True, allow_null=True)

    class Meta:
        model = IMMessageLog
        fields = (
            'id', 'channel', 'channel_name', 'channel_type', 'event',
            'target', 'title', 'content', 'success', 'response', 'created_at',
        )
        read_only_fields = fields


class SendTestSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True, default='ISP Manager 通知')
    content = serializers.CharField()


class DispatchEventSerializer(serializers.Serializer):
    event = serializers.ChoiceField(choices=NotificationEvent.choices)
    title = serializers.CharField(required=False, allow_blank=True, default='ISP Manager 通知')
    content = serializers.CharField()
    action = serializers.CharField(required=False, allow_blank=True, default='custom')
    rule_id = serializers.IntegerField(required=False, allow_null=True)
    context = serializers.JSONField(required=False, default=dict)


class TestEmailSerializer(serializers.Serializer):
    to = serializers.EmailField(required=False, allow_blank=True, default='')


class NotificationPreviewSerializer(serializers.Serializer):
    """仅渲染、不投递的规则模板预览参数。"""

    event = serializers.ChoiceField(choices=NotificationEvent.choices)
    title = serializers.CharField(required=False, allow_blank=True, default='')
    content = serializers.CharField(required=False, allow_blank=True, default='')
    title_template = serializers.CharField(required=False, allow_blank=True, default='')
    template = serializers.CharField(required=False, allow_blank=True, default='')
    format_mode = serializers.ChoiceField(
        choices=NotificationFormatMode.choices,
        required=False,
        default=NotificationFormatMode.DETAILED,
    )
    context = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        try:
            validate_notification_template(attrs.get('title_template', ''), field_label='标题模板')
            validate_notification_template(attrs.get('template', ''), field_label='正文模板')
        except ValueError as exc:
            raise serializers.ValidationError({'template': str(exc)}) from exc
        if attrs.get('format_mode') == NotificationFormatMode.CUSTOM and not attrs.get('template'):
            raise serializers.ValidationError({'template': '自定义格式必须填写正文模板'})
        return attrs


def _user_name(user):
    if not user:
        return None
    return user.get_full_name() or user.username


class SystemBackupSerializer(serializers.ModelSerializer):
    backup_type_label = serializers.CharField(source='get_backup_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    file_size_display = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    download_ready = serializers.SerializerMethodField()

    class Meta:
        model = SystemBackup
        fields = (
            'id', 'name', 'backup_type', 'backup_type_label',
            'status', 'status_label',
            'file_name', 'file_size', 'file_size_display', 'checksum_sha256',
            'source_version', 'note', 'error_message',
            'created_by', 'created_by_name',
            'created_at', 'finished_at', 'download_ready',
        )
        read_only_fields = fields

    def get_file_size_display(self, obj):
        return format_size(obj.file_size)

    def get_created_by_name(self, obj):
        return _user_name(obj.created_by)

    def get_download_ready(self, obj):
        return obj.status == SystemBackup.Status.SUCCESS and bool(obj.storage_path)


class SystemBackupCreateSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    name = serializers.CharField(required=False, allow_blank=True, default='', max_length=160)


class ConfirmActionSerializer(serializers.Serializer):
    confirm = serializers.CharField()

    def validate_confirm(self, value):
        expected = self.context.get('confirm_word') or 'CONFIRM'
        if (value or '').strip() != expected:
            raise serializers.ValidationError(f'请输入 {expected} 以确认')
        return value


class SystemUpgradeSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    file_size_display = serializers.SerializerMethodField()
    uploaded_by_name = serializers.SerializerMethodField()
    applied_by_name = serializers.SerializerMethodField()
    auto_backup_name = serializers.SerializerMethodField()

    class Meta:
        model = SystemUpgradePackage
        fields = (
            'id', 'filename', 'file_size', 'file_size_display', 'checksum_sha256',
            'package_version', 'min_version', 'changelog',
            'status', 'status_label', 'apply_log', 'error_message',
            'from_version', 'to_version',
            'auto_backup', 'auto_backup_name',
            'uploaded_by', 'uploaded_by_name',
            'applied_by', 'applied_by_name',
            'uploaded_at', 'applied_at', 'finished_at',
        )
        read_only_fields = fields

    def get_file_size_display(self, obj):
        return format_size(obj.file_size)

    def get_uploaded_by_name(self, obj):
        return _user_name(obj.uploaded_by)

    def get_applied_by_name(self, obj):
        return _user_name(obj.applied_by)

    def get_auto_backup_name(self, obj):
        if not obj.auto_backup_id:
            return None
        return obj.auto_backup.name


class SystemOsTemplateBriefSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source='display_label', read_only=True)
    os_type_label = serializers.CharField(source='get_os_type_display', read_only=True)
    source_type_label = serializers.CharField(source='get_source_type_display', read_only=True)
    default_user = serializers.SerializerMethodField()
    is_stale = serializers.SerializerMethodField()

    class Meta:
        model = SystemOsTemplate
        fields = (
            'id', 'code', 'name', 'version', 'label',
            'os_type', 'os_type_label', 'source_type', 'source_type_label',
            'enabled', 'sort_order',
            'default_user', 'is_stale',
        )

    def get_default_user(self, obj):
        from .os_templates import default_user_for_template
        return default_user_for_template(obj)

    def get_is_stale(self, obj):
        return is_stale_os_template(obj)


class SystemOsTemplateSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source='display_label', read_only=True)
    os_type_label = serializers.CharField(source='get_os_type_display', read_only=True)
    source_type_label = serializers.CharField(source='get_source_type_display', read_only=True)
    default_user = serializers.SerializerMethodField()
    is_stale = serializers.SerializerMethodField()

    class Meta:
        model = SystemOsTemplate
        fields = (
            'id', 'code', 'name', 'version', 'label',
            'backup_filename', 'download_url',
            'os_type', 'os_type_label', 'source_type', 'source_type_label',
            'default_user', 'is_stale',
            'enabled', 'sort_order', 'remark',
            'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'label', 'os_type_label', 'source_type_label',
            'default_user', 'is_stale', 'created_at', 'updated_at',
        )
        extra_kwargs = {
            'code': {'required': False, 'allow_blank': True},
            'backup_filename': {'required': False, 'allow_blank': True},
            'os_type': {'required': False},
        }

    def get_default_user(self, obj):
        from .os_templates import default_user_for_template
        return default_user_for_template(obj)

    def get_is_stale(self, obj):
        return is_stale_os_template(obj)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        url = (attrs.get('download_url') or getattr(self.instance, 'download_url', '') or '').strip()
        filename = (attrs.get('backup_filename') or getattr(self.instance, 'backup_filename', '') or '').strip()
        if url and not filename:
            filename = filename_from_url(url)
        if not filename:
            raise serializers.ValidationError({'backup_filename': '请填写 Cloud 镜像文件名，或提供可推断文件名的下载地址'})
        if not url:
            raise serializers.ValidationError({
                'download_url': '请填写对应的下载地址，例如 https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img',
            })
        source = attrs.get('source_type') or getattr(self.instance, 'source_type', '') or 'cloud'
        if source in ('cloud', 'backup') and is_vzdump_filename(filename):
            raise serializers.ValidationError({
                'backup_filename': 'QEMU 虚机请使用 Cloud 镜像（.qcow2/.img），不再支持 vzdump/.vma.zst',
            })
        if source == 'cloud' and not is_cloud_image_filename(filename):
            raise serializers.ValidationError({
                'backup_filename': 'Cloud 镜像文件名应为 .qcow2 / .img / .raw，或包含 cloudimg / genericcloud',
            })
        attrs['download_url'] = url
        attrs['backup_filename'] = filename
        if not attrs.get('source_type') and not getattr(self.instance, 'source_type', None):
            attrs['source_type'] = 'cloud'
        name = attrs.get('name', getattr(self.instance, 'name', ''))
        if not attrs.get('os_type'):
            attrs['os_type'] = infer_os_type(name)
        return attrs
