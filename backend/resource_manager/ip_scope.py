"""业务 IP 与隧道段的边界，以及同 POP 同 CIDR 的统计合并。

IP 管理只处理 POP 业务地址；隧道/边缘 p2p 段由「隧道 IP 段」页面管理，
IPBlock.description 以 ``[边缘隧道段]`` 开头作为区分标记。
"""
from __future__ import annotations

from django.db.models import Count, Q

TUNNEL_BLOCK_TAG = '[边缘隧道段]'

_STAT_ATTRS = (
    'ann_ip_total',
    'ann_ip_available',
    'ann_ip_assigned',
    'ann_ip_reserved',
    'ann_ip_blocked',
    'ann_ip_system',
)


def is_tunnel_block_desc(description) -> bool:
    return isinstance(description, str) and description.startswith(TUNNEL_BLOCK_TAG)


def business_block_q(prefix: str = '') -> Q:
    field = f'{prefix}description' if prefix else 'description'
    return ~Q(**{f'{field}__startswith': TUNNEL_BLOCK_TAG})


def exclude_tunnel_blocks(qs):
    return qs.exclude(description__startswith=TUNNEL_BLOCK_TAG)


def exclude_tunnel_addresses(qs):
    return qs.exclude(ip_block__description__startswith=TUNNEL_BLOCK_TAG)


def sibling_business_block_ids(block) -> list[int]:
    """同一 POP、同一 CIDR 下的业务地址块 id（含自身）。"""
    from .models import IPBlock
    return list(
        exclude_tunnel_blocks(
            IPBlock.objects.filter(pop_point=block.pop_point, cidr=block.cidr)
        ).values_list('id', flat=True)
    )


def annotate_ip_block_usage(qs):
    from .models import IPAddress
    return qs.annotate(
        ann_ip_total=Count('ip_addresses'),
        ann_ip_available=Count(
            'ip_addresses',
            filter=Q(ip_addresses__status=IPAddress.IPStatus.AVAILABLE),
        ),
        ann_ip_assigned=Count(
            'ip_addresses',
            filter=Q(ip_addresses__status=IPAddress.IPStatus.ASSIGNED),
        ),
        ann_ip_reserved=Count(
            'ip_addresses',
            filter=Q(ip_addresses__status=IPAddress.IPStatus.RESERVED),
        ),
        ann_ip_blocked=Count(
            'ip_addresses',
            filter=Q(ip_addresses__status=IPAddress.IPStatus.BLOCKED),
        ),
        ann_ip_system=Count(
            'ip_addresses',
            filter=Q(ip_addresses__status=IPAddress.IPStatus.SYSTEM),
        ),
    )


def group_ip_blocks(blocks):
    """按 (pop_point_id, cidr) 合并地址块，累加已 annotate 的用量。

    代表行取每组第一条（保持原排序），并挂上 member_ids / merged_count。
    """
    grouped = {}
    order = []
    for block in blocks:
        key = (block.pop_point_id, block.cidr)
        if key not in grouped:
            grouped[key] = block
            block.member_ids = [block.id]
            order.append(key)
            continue
        primary = grouped[key]
        primary.member_ids.append(block.id)
        for attr in _STAT_ATTRS:
            setattr(
                primary, attr,
                int(getattr(primary, attr, 0) or 0) + int(getattr(block, attr, 0) or 0),
            )
    result = []
    for key in order:
        block = grouped[key]
        block.merged_count = len(getattr(block, 'member_ids', [block.id]))
        result.append(block)
    return result
