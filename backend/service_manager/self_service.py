"""门户重装 / 快照自助。

重装在 POP 已配置节点 API 时下发到 vpopmanager（缺镜像则按中心目录 URL 下载）。
快照仍先落任务与档案，节点 PVE snapshot 接口预留。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.utils import timezone

from system_manager.services import get_setting

from .models import InstanceSnapshot, OsImage, SelfServiceJob

logger = logging.getLogger(__name__)

BUSY_JOB_STATUSES = (SelfServiceJob.Status.PENDING, SelfServiceJob.Status.RUNNING)
ACTIVE_SNAPSHOT_STATUSES = (
    InstanceSnapshot.Status.PENDING,
    InstanceSnapshot.Status.READY,
    InstanceSnapshot.Status.RESTORING,
)
DEFAULT_OS_IMAGES = (
    {'code': 'debian12', 'name': 'Debian 12', 'os_type': 'debian', 'version': '12', 'sort_order': 10},
    {'code': 'ubuntu2404', 'name': 'Ubuntu 24.04', 'os_type': 'ubuntu', 'version': '24.04', 'sort_order': 20},
    {'code': 'centos9', 'name': 'CentOS Stream 9', 'os_type': 'centos', 'version': '9', 'sort_order': 30},
)


def snapshot_retain_count() -> int:
    try:
        value = int(get_setting('vps_snapshot_retain_count', 7) or 7)
    except (TypeError, ValueError):
        value = 7
    return max(1, value)


def ensure_default_os_images() -> int:
    created = 0
    for item in DEFAULT_OS_IMAGES:
        _, was_created = OsImage.objects.get_or_create(
            code=item['code'],
            defaults={
                'name': item['name'],
                'os_type': item['os_type'],
                'version': item['version'],
                'enabled': True,
                'sort_order': item['sort_order'],
                'description': '系统预置镜像目录，节点模板引用可后补',
            },
        )
        if was_created:
            created += 1
    return created


def _instance_for(vps):
    try:
        return vps.service_instance
    except ObjectDoesNotExist:
        return None


def _assert_vps_usable(vps) -> None:
    if getattr(vps, 'status', '') == 'deleting':
        raise ValidationError('VPS 正在删除，无法执行自助操作')


def _busy_job(vps) -> SelfServiceJob | None:
    return (
        SelfServiceJob.objects.filter(vps=vps, status__in=BUSY_JOB_STATUSES)
        .order_by('id')
        .first()
    )


def _notify(job: SelfServiceJob, subject: str, *, detail: str = '') -> None:
    from .notify_helpers import notify_selfservice_event
    notify_selfservice_event(job, subject, detail=detail)


def _vps_cloud_init_network(vps) -> dict:
    """把已分配主 IP / CIDR / 网关带给节点重装，避免只保留 keep_ip 却丢静态配置。"""
    data = {'keep_ip': True}
    qs = getattr(vps, 'ip_addresses', None)
    if qs is None:
        return data
    ip = (
        qs.filter(is_primary=True).select_related('ip_block').first()
        or qs.select_related('ip_block').first()
    )
    if ip is None or not ip.address:
        return data
    data['ip'] = str(ip.address)
    data['prefix'] = ip.effective_prefix_len
    gateway = ip.effective_gateway
    if gateway:
        data['gateway'] = gateway
    return data


def _follow_node_reinstall(job_pk: int, pop_id: int, node_job_id, *, notify: bool = True) -> None:
    """后台等待节点重装任务，避免门户请求阻塞。"""
    from resource_manager.models import PopPoint
    from resource_manager import pve_ops
    from resource_manager.pemanager_client import PemanagerError

    job = SelfServiceJob.objects.filter(pk=job_pk).first()
    pop = PopPoint.objects.filter(pk=pop_id).first()
    if job is None or pop is None:
        return
    try:
        waited = pve_ops.wait_for_create_job(pop, node_job_id)
        if isinstance(waited, dict) and waited.get('status') == 'failed':
            fail_job(job, message=waited.get('error') or '节点重装失败', notify=notify)
            return
        complete_job(job, message='节点已按 Cloud 镜像重装', notify=notify)
    except PemanagerError as exc:
        fail_job(job, message=f'节点重装失败：{exc}', notify=notify)
    except Exception as exc:  # noqa: BLE001
        logger.exception('重装任务 %s 后台跟踪失败', job_pk)
        fail_job(job, message=f'节点重装失败：{exc}', notify=notify)


def request_reinstall(
    vps,
    *,
    os_image: OsImage,
    password: str = '',
    username: str = '',
    requested_by=None,
    notify: bool = True,
    request=None,
) -> SelfServiceJob:
    _assert_vps_usable(vps)
    if not os_image.enabled:
        raise ValidationError({'os_image': '该镜像未上架'})
    busy = _busy_job(vps)
    if busy:
        raise ValidationError(f'已有进行中的自助任务 {busy.job_no}')
    if password and len(password) < 8:
        raise ValidationError({'password': '密码至少 8 位'})

    payload = {
        'os_image_code': os_image.code,
        'os_type': os_image.os_type,
        'os_version': os_image.version,
    }
    if password:
        payload['password'] = password
    if username:
        payload['username'] = username.strip()
    with transaction.atomic():
        job = SelfServiceJob.objects.create(
            job_type=SelfServiceJob.JobType.REINSTALL,
            vps=vps,
            service_instance=_instance_for(vps),
            os_image=os_image,
            request_payload=payload,
            requested_by=requested_by,
            result_message='已提交重装任务，正在下发到节点',
        )
    if notify:
        _notify(job, '提交重装', detail=os_image.name)
    from portal.audit import log_selfservice_action
    log_selfservice_action(
        operator=requested_by,
        vps=vps,
        description=f'提交重装任务 {job.job_no}',
        extra={
            'job_no': job.job_no,
            'os_image': os_image.code,
            'password_set': bool(password),
        },
        request=request,
    )
    from resource_manager.pemanager_client import PemanagerError, pop_has_api_config
    if vps.source_ref and vps.pop_point_id and pop_has_api_config(vps.pop_point):
        from resource_manager import pve_ops
        node_payload = {
            'os_code': getattr(getattr(os_image, 'os_template', None), 'code', '') or os_image.code,
            'filename': os_image.node_template_ref,
            'password': password,
            'keep_ip': True,
        }
        if vps.vcpu:
            node_payload['cores'] = vps.vcpu
        if vps.ram_mb:
            node_payload['memory_mb'] = vps.ram_mb
        if vps.disk_gb:
            node_payload['disk_gb'] = vps.disk_gb
        node_payload.update(_vps_cloud_init_network(vps))
        catalog = getattr(os_image, 'os_template', None)
        chosen_user = (username or '').strip()
        if catalog is not None:
            from system_manager.os_templates import default_user_for_template
            node_payload['username'] = chosen_user or default_user_for_template(catalog)
            if getattr(catalog, 'download_url', ''):
                node_payload['download_url'] = catalog.download_url
        elif chosen_user:
            node_payload['username'] = chosen_user
        if node_payload.get('username'):
            extra_user = dict(job.request_payload or {})
            extra_user['username'] = node_payload['username']
            job.request_payload = extra_user
            job.save(update_fields=['request_payload', 'updated_at'])
        try:
            if catalog is not None:
                template = pve_ops.ensure_os_template_on_pop(vps.pop_point, catalog)
                if template and template.get('id'):
                    node_payload['template_id'] = template['id']
            result = pve_ops.reinstall_vps(vps, node_payload, dry_run=False)
            extra = dict(job.request_payload or {})
            extra['node_job_id'] = result.get('job_id')
            extra['hardware'] = {
                key: node_payload.get(key)
                for key in ('cores', 'memory_mb', 'disk_gb')
                if node_payload.get(key)
            }
            job.request_payload = extra
            job.save(update_fields=['request_payload', 'updated_at'])
            status = result.get('status') if isinstance(result, dict) else None
            if status == 'failed':
                return fail_job(job, message=result.get('error') or '节点重装失败', notify=notify)
            if status == 'succeeded':
                return complete_job(job, message='节点已按 Cloud 镜像重装', notify=notify)
            job.status = SelfServiceJob.Status.RUNNING
            job.result_message = '重装任务已下发到节点'
            job.save(update_fields=['status', 'result_message', 'updated_at'])
            node_job_id = result.get('job_id') if isinstance(result, dict) else None
            if node_job_id:
                job_pk = job.pk
                pop_id = vps.pop_point_id

                def _start():
                    threading.Thread(
                        target=_follow_node_reinstall,
                        args=(job_pk, pop_id, node_job_id),
                        kwargs={'notify': notify},
                        name=f'reinstall-follow-{job_pk}',
                        daemon=True,
                    ).start()

                transaction.on_commit(_start)
            return job
        except PemanagerError as exc:
            return fail_job(job, message=f'节点重装失败：{exc}', notify=notify)
    return job


def request_snapshot(
    vps,
    *,
    name: str = '',
    note: str = '',
    requested_by=None,
    notify: bool = True,
    request=None,
) -> tuple[InstanceSnapshot, SelfServiceJob]:
    _assert_vps_usable(vps)
    busy = _busy_job(vps)
    if busy:
        raise ValidationError(f'已有进行中的自助任务 {busy.job_no}')

    retain = snapshot_retain_count()
    active = InstanceSnapshot.objects.filter(vps=vps, status__in=ACTIVE_SNAPSHOT_STATUSES)
    overflow = active.count() + 1 - retain
    with transaction.atomic():
        if overflow > 0:
            oldest = active.filter(status=InstanceSnapshot.Status.READY).order_by('created_at')
            for snap in oldest[:overflow]:
                snap.status = InstanceSnapshot.Status.EXPIRED
                snap.save(update_fields=['status', 'updated_at'])
            still = InstanceSnapshot.objects.filter(
                vps=vps, status__in=ACTIVE_SNAPSHOT_STATUSES,
            ).count()
            if still >= retain:
                raise ValidationError(f'快照数量已达上限 {retain}，请先删除或等待过期')

        snap = InstanceSnapshot.objects.create(
            vps=vps,
            service_instance=_instance_for(vps),
            name=(name or '').strip() or f'快照 {timezone.now():%Y-%m-%d %H:%M}',
            note=note.strip(),
            created_by=requested_by,
            status=InstanceSnapshot.Status.PENDING,
        )
        job = SelfServiceJob.objects.create(
            job_type=SelfServiceJob.JobType.SNAPSHOT_CREATE,
            vps=vps,
            service_instance=snap.service_instance,
            snapshot=snap,
            request_payload={'snapshot_name': snap.name},
            requested_by=requested_by,
            result_message='等待处理（节点快照接口预留）',
        )
    if notify:
        _notify(job, '提交快照', detail=snap.name)
    from portal.audit import log_selfservice_action
    log_selfservice_action(
        operator=requested_by,
        vps=vps,
        description=f'提交快照任务 {job.job_no}',
        extra={'job_no': job.job_no, 'snapshot_name': snap.name},
        request=request,
    )
    return snap, job


def request_restore(
    vps,
    snapshot: InstanceSnapshot,
    *,
    requested_by=None,
    notify: bool = True,
    request=None,
) -> SelfServiceJob:
    _assert_vps_usable(vps)
    if snapshot.vps_id != vps.pk:
        raise ValidationError({'snapshot': '快照不属于该 VPS'})
    if snapshot.status != InstanceSnapshot.Status.READY:
        raise ValidationError({'snapshot': '仅可用快照可以恢复'})
    busy = _busy_job(vps)
    if busy:
        raise ValidationError(f'已有进行中的自助任务 {busy.job_no}')

    with transaction.atomic():
        snapshot.status = InstanceSnapshot.Status.RESTORING
        snapshot.save(update_fields=['status', 'updated_at'])
        job = SelfServiceJob.objects.create(
            job_type=SelfServiceJob.JobType.SNAPSHOT_RESTORE,
            vps=vps,
            service_instance=_instance_for(vps),
            snapshot=snapshot,
            request_payload={'snapshot_no': snapshot.snapshot_no},
            requested_by=requested_by,
            result_message='等待处理（节点恢复接口预留）',
        )
    if notify:
        _notify(job, '提交恢复', detail=snapshot.snapshot_no)
    from portal.audit import log_selfservice_action
    log_selfservice_action(
        operator=requested_by,
        vps=vps,
        description=f'提交快照恢复任务 {job.job_no}',
        extra={'job_no': job.job_no, 'snapshot_no': snapshot.snapshot_no},
        request=request,
    )
    return job


def complete_job(job: SelfServiceJob, *, message: str = '', notify: bool = True) -> SelfServiceJob:
    if job.status == SelfServiceJob.Status.SUCCEEDED:
        return job
    if job.status == SelfServiceJob.Status.CANCELLED:
        raise ValidationError('任务已取消')

    now = timezone.now()
    vps = job.vps
    payload = dict(job.request_payload or {})

    with transaction.atomic():
        if job.job_type == SelfServiceJob.JobType.REINSTALL and job.os_image_id:
            vps.os_type = job.os_image.os_type
            vps.os_version = job.os_image.version
            updates = ['os_type', 'os_version', 'updated_at']
            password = payload.pop('password', None)
            username = str(payload.get('username') or '').strip()
            if password:
                vps.mgmt_password = password
                if username:
                    vps.mgmt_username = username
                elif not vps.mgmt_username:
                    catalog = getattr(job.os_image, 'os_template', None)
                    if catalog is not None:
                        from system_manager.os_templates import default_user_for_template
                        vps.mgmt_username = default_user_for_template(catalog)
                    else:
                        vps.mgmt_username = 'ubuntu'
                updates.extend(['mgmt_password', 'mgmt_username'])
                payload['password_set'] = True
            vps.save(update_fields=list(dict.fromkeys(updates)))
            job.request_payload = payload
            job.os_image = job.os_image
        elif job.job_type == SelfServiceJob.JobType.SNAPSHOT_CREATE and job.snapshot_id:
            snap = job.snapshot
            snap.status = InstanceSnapshot.Status.READY
            snap.save(update_fields=['status', 'updated_at'])
        elif job.job_type == SelfServiceJob.JobType.SNAPSHOT_RESTORE and job.snapshot_id:
            snap = job.snapshot
            snap.status = InstanceSnapshot.Status.READY
            snap.restored_at = now
            snap.save(update_fields=['status', 'restored_at', 'updated_at'])

        job.status = SelfServiceJob.Status.SUCCEEDED
        job.progress = 100
        job.finished_at = now
        if not job.started_at:
            job.started_at = now
        job.result_message = message or '骨架已完成（未调用节点）'
        job.save(update_fields=[
            'status', 'progress', 'finished_at', 'started_at',
            'result_message', 'request_payload', 'updated_at',
        ])
    if notify:
        _notify(job, '任务完成', detail=job.result_message)
    return job


def fail_job(job: SelfServiceJob, *, message: str = '', notify: bool = True) -> SelfServiceJob:
    if job.status in {SelfServiceJob.Status.SUCCEEDED, SelfServiceJob.Status.CANCELLED}:
        raise ValidationError('已结束的任务不能标记失败')
    now = timezone.now()
    with transaction.atomic():
        if job.job_type == SelfServiceJob.JobType.SNAPSHOT_CREATE and job.snapshot_id:
            job.snapshot.status = InstanceSnapshot.Status.FAILED
            job.snapshot.save(update_fields=['status', 'updated_at'])
        elif job.job_type == SelfServiceJob.JobType.SNAPSHOT_RESTORE and job.snapshot_id:
            job.snapshot.status = InstanceSnapshot.Status.READY
            job.snapshot.save(update_fields=['status', 'updated_at'])
        payload = dict(job.request_payload or {})
        payload.pop('password', None)
        job.request_payload = payload
        job.status = SelfServiceJob.Status.FAILED
        job.finished_at = now
        job.result_message = message or '任务失败'
        job.save(update_fields=[
            'status', 'finished_at', 'result_message', 'request_payload', 'updated_at',
        ])
    if notify:
        _notify(job, '任务失败', detail=job.result_message)
    return job


def cancel_job(job: SelfServiceJob, *, message: str = '', notify: bool = True) -> SelfServiceJob:
    if not job.is_open:
        raise ValidationError('仅待处理或进行中的任务可取消')
    now = timezone.now()
    with transaction.atomic():
        if job.job_type == SelfServiceJob.JobType.SNAPSHOT_CREATE and job.snapshot_id:
            job.snapshot.status = InstanceSnapshot.Status.DELETED
            job.snapshot.save(update_fields=['status', 'updated_at'])
        elif job.job_type == SelfServiceJob.JobType.SNAPSHOT_RESTORE and job.snapshot_id:
            job.snapshot.status = InstanceSnapshot.Status.READY
            job.snapshot.save(update_fields=['status', 'updated_at'])
        payload = dict(job.request_payload or {})
        payload.pop('password', None)
        job.request_payload = payload
        job.status = SelfServiceJob.Status.CANCELLED
        job.finished_at = now
        job.result_message = message or '已取消'
        job.save(update_fields=[
            'status', 'finished_at', 'result_message', 'request_payload', 'updated_at',
        ])
    if notify:
        _notify(job, '取消任务', detail=job.result_message)
    return job


def delete_snapshot(snapshot: InstanceSnapshot, *, requested_by=None, request=None) -> InstanceSnapshot:
    if snapshot.status == InstanceSnapshot.Status.RESTORING:
        raise ValidationError('恢复中的快照不能删除')
    snapshot.status = InstanceSnapshot.Status.DELETED
    snapshot.save(update_fields=['status', 'updated_at'])
    if requested_by is not None or request is not None:
        from portal.audit import log_selfservice_action
        log_selfservice_action(
            operator=requested_by or getattr(request, 'user', None),
            vps=snapshot.vps,
            description=f'删除快照 {snapshot.snapshot_no}',
            extra={'snapshot_no': snapshot.snapshot_no},
            request=request,
        )
    return snapshot


def process_selfservice_jobs(
    *,
    dry_run: bool = False,
    apply: bool = False,
    customer_id: int | None = None,
) -> dict[str, Any]:
    qs = SelfServiceJob.objects.filter(
        status=SelfServiceJob.Status.PENDING,
    ).select_related('vps', 'os_image', 'snapshot', 'service_instance')
    if customer_id:
        qs = qs.filter(vps__customer_id=customer_id)

    stats = {
        'dry_run': dry_run,
        'apply': apply,
        'scanned': 0,
        'completed': 0,
        'skipped': 0,
        'planned': [],
    }
    for job in qs:
        stats['scanned'] += 1
        line = f'{job.job_no} {job.get_job_type_display()} VPS={job.vps.vps_no or job.vps.pk}'
        stats['planned'].append(line)
        if dry_run or not apply:
            stats['skipped'] += 1
            continue
        complete_job(job, message='定时任务骨架完成（未调用节点）')
        stats['completed'] += 1
    return stats
