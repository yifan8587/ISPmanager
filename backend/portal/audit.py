"""门户写操作审计：资源日志 + 关联服务实例日志。"""


def _service_instance(obj):
    if obj is None:
        return None
    try:
        return obj.service_instance
    except Exception:  # noqa: BLE001 — OneToOne 未绑定
        return None


def _portal_instance_subject(description: str, action_type: str) -> str:
    text = description or ''
    if '协议配置' in text:
        return '门户协议配置已更新'
    if '下发' in text:
        return '门户配置已下发'
    if '电源' in text:
        return '门户电源操作'
    if '密码' in text:
        return '门户更新 Root 密码档案'
    from resource_manager.models import ResourceOperationLog
    mapping = {
        ResourceOperationLog.ActionType.CONFIG_CHANGE: '门户协议配置已更新',
        ResourceOperationLog.ActionType.DEPLOY: '门户配置已下发',
        ResourceOperationLog.ActionType.STATUS_CHANGE: '门户电源操作',
        ResourceOperationLog.ActionType.RESTART: '门户电源操作',
    }
    return mapping.get(action_type, text or '门户操作')


def _notify_portal_instance(instance, description, action_type):
    if instance is None:
        return
    from service_manager.notify_helpers import notify_instance_event
    notify_instance_event(
        instance,
        _portal_instance_subject(description, action_type),
        detail=description,
    )


def log_portal_vps_action(request, vps, description, *, action_type=None, extra=None, notify=True):
    from resource_manager.models import ResourceOperationLog, record_resource_log
    from service_manager.models import ServiceOperationLog, _log_service

    resolved_action = action_type or ResourceOperationLog.ActionType.STATUS_CHANGE
    record_resource_log(
        operator=getattr(request, 'user', None),
        action_type=resolved_action,
        description=description,
        target_model=ResourceOperationLog.TargetModel.VPS,
        target_id=vps.pk,
        target_repr=vps.vps_no or vps.name,
        pop_point=getattr(vps, 'pop_point', None),
        vps=vps,
        new_value=extra,
        request=request,
    )
    instance = _service_instance(vps)
    if instance:
        _log_service(
            operator=getattr(request, 'user', None),
            action_type=ServiceOperationLog.ActionType.OTHER,
            description=description,
            service_instance=instance,
            new_value=extra,
            request=request,
        )
        if notify:
            _notify_portal_instance(instance, description, resolved_action)


def log_portal_edge_action(request, edge, description, *, action_type=None, extra=None, notify=True):
    from resource_manager.models import ResourceOperationLog, record_resource_log
    from service_manager.models import ServiceOperationLog, _log_service

    resolved_action = action_type or ResourceOperationLog.ActionType.DEPLOY
    record_resource_log(
        operator=getattr(request, 'user', None),
        action_type=resolved_action,
        description=description,
        target_model=ResourceOperationLog.TargetModel.EDGE,
        target_id=edge.pk,
        target_repr=edge.edge_no or edge.name,
        pop_point=getattr(edge, 'pop_point', None),
        edge=edge,
        new_value=extra,
        request=request,
    )
    instance = _service_instance(edge)
    if instance:
        _log_service(
            operator=getattr(request, 'user', None),
            action_type=ServiceOperationLog.ActionType.OTHER,
            description=description,
            service_instance=instance,
            new_value=extra,
            request=request,
        )
        if notify:
            _notify_portal_instance(instance, description, resolved_action)


def log_selfservice_action(*, operator, vps, description, extra=None, request=None):
    """自助引擎（重装/快照）审计；禁止写入明文密码。"""
    from resource_manager.models import ResourceOperationLog, record_resource_log
    from service_manager.models import ServiceOperationLog, _log_service

    record_resource_log(
        operator=operator,
        action_type=ResourceOperationLog.ActionType.OTHER,
        description=description,
        target_model=ResourceOperationLog.TargetModel.VPS,
        target_id=getattr(vps, 'pk', None),
        target_repr=(getattr(vps, 'vps_no', None) or getattr(vps, 'name', '')) if vps else '',
        pop_point=getattr(vps, 'pop_point', None) if vps else None,
        vps=vps,
        new_value=extra,
        request=request,
    )
    instance = _service_instance(vps)
    if instance:
        _log_service(
            operator=operator,
            action_type=ServiceOperationLog.ActionType.OTHER,
            description=description,
            service_instance=instance,
            new_value=extra,
            request=request,
        )
