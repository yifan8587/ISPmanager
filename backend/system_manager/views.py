from django.db import transaction
from django.http import FileResponse
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from accounts.drf_mixins import SystemSystemPermissionMixin
from accounts.permissions import ensure_user_profile
from accounts.role_permissions import check_setting_manage_permission

from . import im_services
from .maintenance import (
    MaintenanceError,
    delete_backup_file,
    delete_upgrade_file,
    maintenance_summary,
    register_upgrade_package,
    register_uploaded_backup,
    resolve_backup_file,
    start_backup,
    start_restore,
    start_upgrade,
)
from .models import (
    IMChannel, IMChannelType, IMMessageLog, NotificationEvent, NotificationRule,
    PaymentChannel, SystemBackup, SystemOsTemplate, SystemSetting, SystemUpgradePackage,
)
from .serializers import (
    ConfirmActionSerializer,
    DispatchEventSerializer,
    TestEmailSerializer,
    IMChannelSerializer,
    IMMessageLogSerializer,
    NotificationPreviewSerializer,
    NotificationRuleSerializer,
    PaymentChannelSerializer,
    SendTestSerializer,
    SystemBackupCreateSerializer,
    SystemBackupSerializer,
    SystemOsTemplateSerializer,
    SystemSettingSerializer,
    SystemSettingWriteSerializer,
    SystemSettingBulkUpdateSerializer,
    SystemUpgradeSerializer,
)
from .version import get_system_version
from .notification_templates import event_catalog_payload, render_rule_message
from .payment_providers import PAYMENT_PROVIDER_SPECS


class SystemSettingViewSet(SystemSystemPermissionMixin, viewsets.ModelViewSet):
    """
    系统配置 CRUD

    GET  /api/system/settings/              列表
    GET  /api/system/settings/grouped/      按分类分组
    GET  /api/system/settings/public/       公开配置（无需登录）
    POST /api/system/settings/bulk_update/   批量更新
    """
    extra_read_actions = frozenset({'grouped', 'public_settings', 'categories', 'currencies'})

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'value_type', 'is_public', 'is_editable']
    search_fields = ['key', 'label', 'description']
    ordering_fields = ['category', 'sort_order', 'key', 'updated_at']
    ordering = ['category', 'sort_order', 'key']
    http_method_names = ['get', 'head', 'options', 'patch', 'post']

    def get_queryset(self):
        return SystemSetting.objects.select_related('updated_by').all()

    def get_serializer_class(self):
        if self.action in ('partial_update', 'update'):
            return SystemSettingWriteSerializer
        return SystemSettingSerializer

    def create(self, request, *args, **kwargs):
        return Response({'detail': '请通过迁移或后台初始化配置项'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if not obj.is_editable:
            return Response({'detail': '内置配置项不可删除'}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    def perform_update(self, serializer):
        if not serializer.instance.is_editable:
            raise PermissionDenied('该配置项不可编辑')
        profile = ensure_user_profile(self.request.user)
        if not check_setting_manage_permission(profile, serializer.instance.key):
            raise PermissionDenied(f'无权修改配置项 {serializer.instance.key}')
        serializer.save(updated_by=self.request.user)

    @action(detail=False, methods=['get'], url_path='grouped')
    def grouped(self, request):
        """按分类返回配置项"""
        qs = self.filter_queryset(self.get_queryset())
        groups = {}
        for setting in qs:
            cat = setting.category
            if cat not in groups:
                groups[cat] = {
                    'category': cat,
                    'category_label': setting.get_category_display(),
                    'items': [],
                }
            groups[cat]['items'].append(SystemSettingSerializer(setting).data)
        category_order = {value: i for i, value in enumerate(SystemSetting.Category.values)}
        ordered = sorted(
            groups.values(),
            key=lambda g: category_order.get(g['category'], 99),
        )
        return Response(ordered)

    @action(detail=False, methods=['get'], url_path='categories')
    def categories(self, request):
        return Response([
            {'value': c[0], 'label': c[1]}
            for c in SystemSetting.Category.choices
        ])

    @action(detail=False, methods=['get'], url_path='currencies')
    def currencies(self, request):
        """币种枚举 + 当前汇率（用于账单/产品换算展示）。"""
        from . import currency_services as cs
        return Response(cs.currency_payload())

    @action(
        detail=False, methods=['get'], url_path='public',
        permission_classes=[AllowAny],
        authentication_classes=[],
    )
    def public_settings(self, request):
        """公开配置（站点名称等），登录页可用"""
        qs = SystemSetting.objects.filter(is_public=True).order_by('sort_order')
        data = {
            s.key: SystemSettingSerializer(s).data.get('typed_value')
            for s in qs
        }
        return Response(data)

    @action(detail=False, methods=['post'], url_path='bulk_update')
    def bulk_update(self, request):
        """批量更新配置值"""
        ser = SystemSettingBulkUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        updated = []
        profile = ensure_user_profile(request.user)
        with transaction.atomic():
            for item in ser.validated_data['items']:
                setting = SystemSetting.objects.get(key=item['key'])
                if not setting.is_editable:
                    return Response(
                        {'detail': f'配置项 {setting.key} 不可编辑'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if not check_setting_manage_permission(profile, setting.key):
                    return Response(
                        {'detail': f'无权修改配置项 {setting.key}'},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                write_ser = SystemSettingWriteSerializer(
                    setting, data={'value': item['value']}, partial=True,
                )
                write_ser.is_valid(raise_exception=True)
                write_ser.save(updated_by=request.user)
                updated.append(setting.key)
        qs = SystemSetting.objects.filter(key__in=updated).select_related('updated_by')
        return Response(SystemSettingSerializer(qs, many=True).data)


class SystemOsTemplateViewSet(SystemSystemPermissionMixin, viewsets.ModelViewSet):
    """系统模版目录：产品管理、门户下单与 POP 自动开通共用。"""

    queryset = SystemOsTemplate.objects.all()
    serializer_class = SystemOsTemplateSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['os_type', 'source_type', 'enabled']
    search_fields = ['name', 'version', 'code', 'backup_filename', 'download_url']
    ordering_fields = ['sort_order', 'name', 'version', 'updated_at']
    ordering = ['sort_order', 'id']
    view_permission = 'system.os_template.view'
    manage_permission = 'system.os_template.manage'
    extra_read_actions = frozenset({'os_types'})

    @action(detail=False, methods=['get'], url_path='os_types')
    def os_types(self, request):
        return Response([
            {'value': value, 'label': label}
            for value, label in SystemOsTemplate.OSType.choices
        ])

    @action(detail=False, methods=['post'], url_path='sync_from_pop')
    def sync_from_pop(self, request):
        """从指定 POP 的 PVE Cloud 目录同步到中心系统模版（已配好的 Cloud 不覆盖，旧 vma 会升级）。"""
        from resource_manager.models import PopPoint
        from .os_templates import sync_os_catalog_from_pop_report

        pop = PopPoint.objects.filter(pk=request.data.get('pop_id')).first()
        if pop is None:
            return Response({'detail': '请选择有效的 POP 点'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(sync_os_catalog_from_pop_report(pop))

    @action(detail=False, methods=['post'], url_path='seed_defaults')
    def seed_defaults(self, request):
        """导入官方 Cloud 目录，升级仍登记为 vzdump/.vma.zst 的同名版本。"""
        from .os_templates import seed_default_os_templates

        return Response(seed_default_os_templates())


class PaymentChannelViewSet(SystemSystemPermissionMixin, viewsets.ModelViewSet):
    """在线支付渠道配置（密钥只写入，不会通过接口回显）。"""

    queryset = PaymentChannel.objects.all()
    serializer_class = PaymentChannelSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['provider', 'enabled', 'sandbox']
    search_fields = ['name', 'remark']
    ordering_fields = ['sort_order', 'provider', 'name', 'updated_at']
    ordering = ['sort_order', 'provider', 'id']
    extra_read_actions = frozenset({'providers'})

    def destroy(self, request, *args, **kwargs):
        channel = self.get_object()
        if channel.payment_transactions.exists():
            return Response(
                {'detail': '该渠道已有支付交易记录，不能删除；请改为停用。'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['get'], url_path='providers')
    def providers(self, request):
        return Response([
            {
                'value': provider,
                'label': spec['label'],
                'description': spec['description'],
                'checkout_mode': spec['checkout_mode'],
                'fields': spec['fields'],
                'recommended_events': spec.get('recommended_events', []),
                'documentation_url': spec.get('documentation_url', ''),
                'callback_usage': spec.get('callback_usage', ''),
            }
            for provider, spec in PAYMENT_PROVIDER_SPECS.items()
        ])


class IMChannelViewSet(SystemSystemPermissionMixin, viewsets.ModelViewSet):
    """IM 通道配置 CRUD + 连接测试 + 发送。

    POST /api/system/im-channels/{id}/test/          发送测试消息
    POST /api/system/im-channels/{id}/send/          向该通道发送自定义消息
    GET  /api/system/im-channels/channel_types/      通道类型枚举
    """
    extra_read_actions = frozenset({'channel_types'})
    queryset = IMChannel.objects.all()
    serializer_class = IMChannelSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['channel_type', 'enabled']
    search_fields = ['name', 'remark']
    ordering = ['-created_at']

    @action(detail=False, methods=['get'], url_path='channel_types')
    def channel_types(self, request):
        return Response([{'value': v, 'label': l} for v, l in IMChannelType.choices])

    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        channel = self.get_object()
        return Response(im_services.test_channel(channel))

    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        channel = self.get_object()
        ser = SendTestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        result = im_services.send_to_channel(
            channel, ser.validated_data.get('title') or 'ISP Manager 通知',
            ser.validated_data['content'], event='custom',
        )
        return Response(result)


class NotificationRuleViewSet(SystemSystemPermissionMixin, viewsets.ModelViewSet):
    """通知规则 CRUD：变更/异动事件 → IM 通道路由。

    GET  /api/system/notification-rules/events/      事件、动作、变量枚举
    POST /api/system/notification-rules/preview/     预览模板，不实际发送
    POST /api/system/notification-rules/dispatch/    手动按事件分发一条消息
    """
    extra_read_actions = frozenset({'events'})
    queryset = NotificationRule.objects.prefetch_related('channels').all()
    serializer_class = NotificationRuleSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['enabled']
    search_fields = ['name', 'remark']

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        event = self.request.query_params.get('event')
        if not event:
            return queryset
        matched_ids = [
            rule.pk for rule in queryset
            if rule.matches_event(event)
        ]
        return queryset.filter(pk__in=matched_ids)

    @action(detail=False, methods=['get'], url_path='events')
    def events(self, request):
        return Response(event_catalog_payload())

    @action(detail=False, methods=['post'], url_path='preview')
    def preview(self, request):
        serializer = NotificationPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        title, content, context = render_rule_message(
            data['event'],
            title=data.get('title', ''),
            content=data.get('content', ''),
            context=data.get('context') or {},
            title_template=data.get('title_template', ''),
            content_template=data.get('template', ''),
            format_mode=data.get('format_mode', 'detailed'),
        )
        return Response({
            'event': data['event'],
            'title': title,
            'content': content,
            'context': context,
        })

    @action(detail=False, methods=['post'], url_path='dispatch')
    def dispatch_event(self, request):
        ser = DispatchEventSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        context = dict(data.get('context') or {})
        context.setdefault('action', data.get('action') or 'custom')
        context.setdefault('action_label', '手动触发')
        context.setdefault('module', '系统管理')
        context.setdefault('operator_name', request.user.get_full_name() or request.user.username)
        result = im_services.dispatch_event(
            data['event'],
            data.get('title') or 'ISP Manager 通知',
            data['content'],
            context=context,
            skip_action_filters=True,
            rule_id=data.get('rule_id'),
        )
        return Response(result)

    @action(detail=False, methods=['post'], url_path='test-email')
    def test_email(self, request):
        ser = TestEmailSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        from .email_services import send_test_email
        to = ser.validated_data.get('to') or ''
        if not to and getattr(request.user, 'email', ''):
            to = request.user.email
        result = send_test_email(to)
        status_code = status.HTTP_200_OK if result.get('ok') else status.HTTP_400_BAD_REQUEST
        return Response(result, status=status_code)


class IMMessageLogViewSet(SystemSystemPermissionMixin, viewsets.ReadOnlyModelViewSet):
    """IM 消息发送日志（只读）。"""
    queryset = IMMessageLog.objects.select_related('channel').all()
    serializer_class = IMMessageLogSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['channel', 'channel_type', 'event', 'success']
    search_fields = ['title', 'content', 'target']
    ordering = ['-created_at']


class SystemOverviewViewSet(SystemSystemPermissionMixin, viewsets.ViewSet):
    """GET /api/system/overview/  系统概览（设置模块首页）"""

    extra_read_actions = frozenset({'list'})

    def list(self, request):
        from django.contrib.auth.models import User
        from customer_manager.models import Customer
        from accounts.models import UserProfile

        setting_count = SystemSetting.objects.count()
        user_count = User.objects.filter(is_active=True).count()
        customer_count = Customer.objects.count()
        version = get_system_version()
        return Response({
            'setting_count': setting_count,
            'active_user_count': user_count,
            'customer_count': customer_count,
            'version': version['version'],
            'version_display': version['display'],
            'product': version['product'],
            'account_types': [
                {'value': k, 'label': v, 'count': UserProfile.objects.filter(account_type=k).count()}
                for k, v in UserProfile.AccountType.choices
            ],
        })


class SystemVersionViewSet(SystemSystemPermissionMixin, viewsets.ViewSet):
    """GET /api/system/version/  当前系统版本与维护摘要。"""

    extra_read_actions = frozenset({'list'})

    def list(self, request):
        return Response(maintenance_summary())


class SystemBackupViewSet(SystemSystemPermissionMixin, viewsets.ModelViewSet):
    """业务数据在线备份 / 下载 / 还原。"""

    extra_read_actions = frozenset({'download'})
    serializer_class = SystemBackupSerializer
    http_method_names = ['get', 'post', 'head', 'options', 'delete']
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['backup_type', 'status']
    search_fields = ['name', 'note', 'file_name', 'source_version']
    ordering = ['-created_at']

    def get_queryset(self):
        return SystemBackup.objects.select_related('created_by').all()

    def create(self, request, *args, **kwargs):
        ser = SystemBackupCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            record = start_backup(
                backup_type=SystemBackup.BackupType.DATA,
                note=ser.validated_data.get('note') or '',
                name=ser.validated_data.get('name') or '',
                user=request.user,
            )
        except MaintenanceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SystemBackupSerializer(record).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.status in (SystemBackup.Status.PENDING, SystemBackup.Status.RUNNING):
            return Response({'detail': '进行中的备份不能删除'}, status=status.HTTP_400_BAD_REQUEST)
        delete_backup_file(obj)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['get'], url_path='download')
    def download(self, request, pk=None):
        backup = self.get_object()
        if backup.status != SystemBackup.Status.SUCCESS:
            return Response({'detail': '备份尚未完成，无法下载'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            path = resolve_backup_file(backup)
        except MaintenanceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_404_NOT_FOUND)
        return FileResponse(path.open('rb'), as_attachment=True, filename=backup.file_name or path.name)

    @action(detail=True, methods=['post'], url_path='restore')
    def restore(self, request, pk=None):
        backup = self.get_object()
        ser = ConfirmActionSerializer(data=request.data, context={'confirm_word': 'RESTORE'})
        ser.is_valid(raise_exception=True)
        try:
            safety = start_restore(backup, user=request.user)
        except MaintenanceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'detail': '已开始还原：将先自动备份当前数据，再导入所选备份。',
            'backup': SystemBackupSerializer(backup).data,
            'safety_backup': SystemBackupSerializer(safety).data,
        })

    @action(
        detail=False, methods=['post'], url_path='upload',
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload(self, request):
        uploaded = request.FILES.get('file')
        if not uploaded:
            return Response({'detail': '未收到文件（字段名 file）'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            record = register_uploaded_backup(
                uploaded=uploaded,
                note=request.data.get('note') or '',
                user=request.user,
            )
        except MaintenanceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SystemBackupSerializer(record).data, status=status.HTTP_201_CREATED)


class SystemUpgradeViewSet(SystemSystemPermissionMixin, viewsets.ModelViewSet):
    """上传更新包并在线升级系统版本。"""

    serializer_class = SystemUpgradeSerializer
    http_method_names = ['get', 'post', 'head', 'options', 'delete']
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status']
    search_fields = ['filename', 'package_version', 'changelog']
    ordering = ['-uploaded_at']

    def get_queryset(self):
        return SystemUpgradePackage.objects.select_related(
            'uploaded_by', 'applied_by', 'auto_backup',
        ).all()

    def create(self, request, *args, **kwargs):
        return Response(
            {'detail': '请使用 /api/system/upgrades/upload/ 上传更新包'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.status == SystemUpgradePackage.Status.APPLYING:
            return Response({'detail': '升级进行中，不能删除'}, status=status.HTTP_400_BAD_REQUEST)
        delete_upgrade_file(obj)
        return super().destroy(request, *args, **kwargs)

    @action(
        detail=False, methods=['post'], url_path='upload',
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload(self, request):
        uploaded = request.FILES.get('file')
        if not uploaded:
            return Response({'detail': '未收到文件（字段名 file）'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            record = register_upgrade_package(uploaded=uploaded, user=request.user)
        except MaintenanceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SystemUpgradeSerializer(record).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='apply')
    def apply(self, request, pk=None):
        package = self.get_object()
        ser = ConfirmActionSerializer(data=request.data, context={'confirm_word': 'UPGRADE'})
        ser.is_valid(raise_exception=True)
        try:
            start_upgrade(package, user=request.user)
        except MaintenanceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        package.refresh_from_db()
        return Response({
            'detail': '已开始升级：将先自动备份业务数据，再应用更新包。',
            'upgrade': SystemUpgradeSerializer(package).data,
        })
