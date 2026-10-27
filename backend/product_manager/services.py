"""
product_manager/services.py
产品容量 / 库存动态计算 与 阶梯定价

核心能力：
  pop_capacity(pop)        POP 点硬件总量（CPU/内存/硬盘/带宽/IP）
  pop_usage(pop)           POP 点已用资源（汇总该 POP 下 VPS / 边缘加速）
  pop_available(pop)       POP 点剩余可用资源（已扣预占）
  pop_held(pop)            POP 点当前预占容量
  check_spec_against_pop() 判断某规格在该 POP 是否满足，返回 (ok, 详情)
  estimate_stock()         估算该 POP 还能开通多少个该规格产品（库存）
  compute_price()          按阶梯升级规则计算定制规格的月租 / 信誉值价格

说明：
  - 内存统一以 MB 计；硬盘以 GB 计；带宽以 Mbps 计。
  - POP 点未提供数值（如 CPU 核数、内存、硬盘）时，对应资源视为「不限制」，
    不参与是否满足/库存的判断（返回 None 表示未知）。
"""
from __future__ import annotations

import re
from decimal import Decimal

from django.db.models import Sum


# 资源键 → 规格字段名 / 单位
RESOURCE_KEYS = ('cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps', 'ip_count', 'nic_count')


def _pop_total_disk_gb(pop) -> int | None:
    """从 PopPoint.disk_info 文本尽力解析总磁盘容量(GB)。无法解析返回 None。"""
    text = (getattr(pop, 'disk_info', '') or '').strip()
    if not text:
        return None
    total = 0
    found = False
    # 匹配如 "2×480GB SSD" / "2x1TB" / "960GB" / "1.92TB"
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*(?:[x×]\s*)?(\d+(?:\.\d+)?)?\s*(TB|GB)', text, re.IGNORECASE):
        a, b, unit = m.group(1), m.group(2), m.group(3).upper()
        if b:  # a×b 形式：数量 × 单盘容量
            size = float(a) * float(b)
        else:
            size = float(a)
        if unit == 'TB':
            size *= 1024
        total += int(size)
        found = True
    return total if found else None


def pop_capacity(pop) -> dict:
    """POP 点硬件总量。值为 None 表示该项未知（不参与限制）。"""
    cpu = pop.cpu_cores if pop.cpu_cores else None
    ram_mb = pop.ram_gb * 1024 if pop.ram_gb else None
    # 优先使用管理员填写的数值容量，其次回退解析 disk_info 文本
    disk_gb = getattr(pop, 'disk_gb', None) or _pop_total_disk_gb(pop)
    bandwidth = pop.total_bandwidth_mbps or None
    ip_total = _pop_ip_total(pop)
    return {
        'cpu_cores': cpu,
        'ram_mb': ram_mb,
        'disk_gb': disk_gb,
        'bandwidth_mbps': bandwidth,
        'ip_count': ip_total,
    }


def _pop_ip_total(pop) -> int | None:
    """POP 下 IP 地址总数（可用 + 已分配）。无地址块返回 None。"""
    from resource_manager.models import IPAddress
    cnt = IPAddress.objects.filter(ip_block__pop_point=pop).count()
    return cnt or None


def _pop_ip_available(pop) -> int:
    """POP 下可用（available）IP 数量。"""
    from resource_manager.models import IPAddress
    return IPAddress.objects.filter(
        ip_block__pop_point=pop, status=IPAddress.IPStatus.AVAILABLE,
    ).count()


def pop_usage(pop) -> dict:
    """POP 已用资源（汇总该 POP 下全部 VPS + 边缘加速）。"""
    from resource_manager.models import VPS, EdgeAcceleration

    vps_qs = VPS.objects.filter(pop_point=pop)
    agg = vps_qs.aggregate(
        cpu=Sum('vcpu'), ram=Sum('ram_mb'), disk=Sum('disk_gb'), bw=Sum('bandwidth_mbps'),
    )
    edge_bw = EdgeAcceleration.objects.filter(pop_point=pop).aggregate(
        bw=Sum('bandwidth_mbps'))['bw'] or 0
    ip_used = _pop_ip_used(pop)
    return {
        'cpu_cores': agg['cpu'] or 0,
        'ram_mb': agg['ram'] or 0,
        'disk_gb': agg['disk'] or 0,
        'bandwidth_mbps': (agg['bw'] or 0) + edge_bw,
        'ip_count': ip_used,
    }


def _pop_ip_used(pop) -> int:
    from resource_manager.models import IPAddress
    return IPAddress.objects.filter(
        ip_block__pop_point=pop,
        status__in=[IPAddress.IPStatus.ASSIGNED, IPAddress.IPStatus.RESERVED],
    ).count()


# ── 容量计算系数（集中可调）─────────────────────────────────
# CPU 超分比（vCPU : pCPU）：>1 表示允许 CPU 超售；内存/硬盘不超分
CPU_OVERCOMMIT_RATIO = 2.0
# 主机系统预留余量（不可用于售卖的部分，按比例预留给宿主机/系统盘开销）
RAM_RESERVE_RATIO = 0.10   # 预留 10% 内存
DISK_RESERVE_RATIO = 0.10  # 预留 10% 硬盘
BANDWIDTH_RESERVE_RATIO = 0.0


def pop_effective_capacity(pop) -> dict:
    """可售卖的有效容量 = 物理容量 × 超分比 − 系统预留。

    None 表示该项未知（不参与限制判定）。
    """
    cap = pop_capacity(pop)
    eff = {}
    cpu = cap.get('cpu_cores')
    eff['cpu_cores'] = None if cpu is None else int(cpu * CPU_OVERCOMMIT_RATIO)
    ram = cap.get('ram_mb')
    eff['ram_mb'] = None if ram is None else int(ram * (1 - RAM_RESERVE_RATIO))
    disk = cap.get('disk_gb')
    eff['disk_gb'] = None if disk is None else int(disk * (1 - DISK_RESERVE_RATIO))
    bw = cap.get('bandwidth_mbps')
    eff['bandwidth_mbps'] = None if bw is None else int(bw * (1 - BANDWIDTH_RESERVE_RATIO))
    eff['ip_count'] = cap.get('ip_count')
    return eff


def pop_held(pop, exclude_order=None) -> dict:
    """当前仍处于预占态的容量。``exclude_order`` 用于开通校验时排除本单已锁库存。"""
    zeros = {k: 0 for k in ('cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps', 'ip_count')}
    if pop is None:
        return zeros
    try:
        from service_manager.capacity_reservation import held_usage
        return held_usage(pop, exclude_order=exclude_order)
    except Exception:  # noqa: BLE001
        return zeros


def pop_available(pop, exclude_order=None) -> dict:
    """POP 剩余可用资源 = 有效容量 − 已用 − 预占。容量未知项返回 None。"""
    if pop is None:
        return {
            'cpu_cores': None, 'ram_mb': None, 'disk_gb': None,
            'bandwidth_mbps': None, 'ip_count': None,
        }
    eff = pop_effective_capacity(pop)
    used = pop_usage(pop)
    held = pop_held(pop, exclude_order=exclude_order)
    avail = {}
    for k in ('cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps'):
        total = eff.get(k)
        avail[k] = None if total is None else max(0, total - used.get(k, 0) - held.get(k, 0))
    # 无 IP 地址池时与 CPU 未填一致：不限制。有地址池则扣减预占。
    if _pop_ip_total(pop) is None:
        avail['ip_count'] = None
    else:
        avail['ip_count'] = max(0, _pop_ip_available(pop) - held.get('ip_count', 0))
    return avail


_RES_LABELS = {
    'cpu_cores': 'CPU 核',
    'ram_mb': '内存(GB)',
    'disk_gb': '硬盘(GB)',
    'bandwidth_mbps': '带宽(Mbps)',
    'ip_count': 'IP 数量',
}


def _amount_text(key: str, value) -> str:
    """管理端展示用 GB；库存比较和节点调用仍使用 MB。"""
    if key == 'ram_mb':
        gb = int(value or 0) / 1024
        shown = int(gb) if gb == int(gb) else round(gb, 2)
        return f'{shown} GB'
    return str(value)

_CHECK_KEYS = ('cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps', 'ip_count')


def check_spec_against_pop(pop, spec: dict, exclude_order=None) -> dict:
    """判断某规格在该 POP 是否资源充足。

    返回 {
      'ok': bool, 'shortages': [..],
      'detail': {res: {label, need, total, used, held, available, usage_pct}}
    }
    容量未知的资源项（total=None）不参与判定。
    """
    if pop is None:
        return {'ok': False, 'shortages': ['未指定 POP 节点'], 'detail': {}}
    eff = pop_effective_capacity(pop)
    used = pop_usage(pop)
    held = pop_held(pop, exclude_order=exclude_order)
    avail = pop_available(pop, exclude_order=exclude_order)
    detail = {}
    shortages = []
    for k in _CHECK_KEYS:
        need = int(spec.get(k, 0) or 0)
        total = eff.get(k)
        a = avail.get(k)
        h = held.get(k, 0)
        u = used.get(k, 0) if k != 'ip_count' else None
        usage_pct = None
        if total:
            consumed = total - a if a is not None else ((u or 0) + h)
            usage_pct = round(min(100.0, max(0.0, (consumed or 0) / total * 100)), 1)
        detail[k] = {
            'label': _RES_LABELS[k], 'need': need,
            'total': total, 'used': u, 'held': h, 'available': a, 'usage_pct': usage_pct,
        }
        if need <= 0 or a is None:
            continue
        if need > a:
            shortages.append(f'{_RES_LABELS[k]} 不足：需 {_amount_text(k, need)}，剩余 {_amount_text(k, a)}')
    return {'ok': not shortages, 'shortages': shortages, 'detail': detail}


def estimate_stock(pop, spec: dict, exclude_order=None) -> dict:
    """估算该 POP 还能开通多少个该规格产品（取各受限资源的最小值/木桶效应）。

    返回 {
      'stock': int|None, 'limiting': res|None, 'limiting_label': str|None,
      'by_resource': {res: {need, available, count}|None}
    }
    - 某资源 need=0 或容量未知 → 不参与（None）
    - 全部资源都不受限 → stock=None（表示不受硬件限制/未知）
    """
    if pop is None:
        return {
            'stock': None, 'limiting': None, 'limiting_label': None, 'by_resource': {},
        }
    avail = pop_available(pop, exclude_order=exclude_order)
    by_resource = {}
    stock = None
    limiting = None
    for k in _CHECK_KEYS:
        need = int(spec.get(k, 0) or 0)
        a = avail.get(k)
        if need <= 0 or a is None:
            by_resource[k] = None
            continue
        c = a // need
        by_resource[k] = {'need': need, 'available': a, 'count': c}
        if stock is None or c < stock:
            stock = c
            limiting = k
    return {
        'stock': stock,
        'limiting': limiting,
        'limiting_label': _RES_LABELS.get(limiting) if limiting else None,
        'by_resource': by_resource,
    }


# ─────────────────────────────────────────────────────────────
# 阶梯定价
# ─────────────────────────────────────────────────────────────

# 升级规则资源键 → 规格字段；ram/disk 以 GB 计（规格内存为 MB，需换算）
_UPGRADE_FIELD = {
    'cpu': ('cpu_cores', 1),
    'ram': ('ram_mb', 1024),   # 规则按 GB，规格按 MB
    'disk': ('disk_gb', 1),
    'bandwidth': ('bandwidth_mbps', 1),
    'ip': ('ip_count', 1),
    'nic': ('nic_count', 1),
}


def _upgrade_qty(kind: str, base_spec: dict, custom_spec: dict) -> int:
    field, divisor = _UPGRADE_FIELD[kind]
    base_val = int(base_spec.get(field, 0) or 0)
    cust_val = int(custom_spec.get(field, base_val) or 0)
    base_units = base_val // divisor
    cust_units = cust_val // divisor
    return max(0, cust_units - base_units)


def _tiered_cost(rules: list, qty: int) -> tuple[Decimal, Decimal]:
    """按阶梯规则对 qty 个升级单位计价，返回 (月租增量, 信誉值增量)。

    每个升级单位 i (1..qty) 落入满足 min_qty<=i<=max_qty 的规则区间。
    """
    if qty <= 0 or not rules:
        return Decimal('0'), Decimal('0')
    price = Decimal('0')
    reputation = Decimal('0')
    # rules 已按 min_qty 排序
    for i in range(1, qty + 1):
        for r in rules:
            lo = r.min_qty or 0
            hi = r.max_qty
            if i >= max(1, lo) and (hi is None or i <= hi):
                price += Decimal(str(r.unit_price))
                reputation += Decimal(str(r.unit_reputation))
                break
    return price, reputation


def is_edge_product(product) -> bool:
    return getattr(product, 'category', None) == 'edge'


def normalize_spec_for_category(product, spec: dict | None = None) -> dict:
    """边缘产品只保留带宽 / IP，避免把 VPS 默认内存带进报价与开通快照。"""
    out = dict(spec or {})
    if is_edge_product(product):
        out['cpu_cores'] = 0
        out['ram_mb'] = 0
        out['disk_gb'] = 0
        out['nic_count'] = 0
    return out


def product_base_spec(product) -> dict:
    """阶梯升级的基准规格：优先取所选模版规格，其次取产品自身规格。

    即「超出模版」后的部分才计入阶梯升级价。
    """
    src = product.template if product.template_id else product
    return normalize_spec_for_category(product, {
        'cpu_cores': src.cpu_cores,
        'ram_mb': src.ram_mb,
        'disk_gb': src.disk_gb,
        'bandwidth_mbps': src.bandwidth_mbps,
        'ip_count': src.ip_count,
        'nic_count': src.nic_count,
    })


def compute_price(product, custom_spec: dict | None = None) -> dict:
    """计算产品在定制规格下的月租 / 信誉值价格。

    阶梯升级以「模版规格」为基准（超出模版的部分逐档计价）。
    custom_spec 为 None 时返回基础定价。
    """
    base_spec = product_base_spec(product)
    monthly = Decimal(str(product.monthly_price))
    reputation = Decimal(str(product.reputation_price))
    breakdown = []

    if custom_spec:
        custom_spec = normalize_spec_for_category(product, custom_spec)
        rules = list(product.upgrade_rules.filter(is_active=True).order_by('resource_kind', 'min_qty'))
        rules_by_kind: dict[str, list] = {}
        for r in rules:
            rules_by_kind.setdefault(r.resource_kind, []).append(r)
        for kind, krules in rules_by_kind.items():
            qty = _upgrade_qty(kind, base_spec, custom_spec)
            if qty <= 0:
                continue
            p, rep = _tiered_cost(krules, qty)
            if p or rep:
                monthly += p
                reputation += rep
                breakdown.append({
                    'resource_kind': kind,
                    'upgrade_qty': qty,
                    'monthly_delta': str(p),
                    'reputation_delta': str(rep),
                })

    from .billing_mode import billing_profile
    return {
        'monthly_price': str(monthly),
        'reputation_price': str(reputation),
        'setup_price': str(product.setup_price),
        'currency': product.currency or 'USD',
        'base_monthly_price': str(product.monthly_price),
        'base_reputation_price': str(product.reputation_price),
        'breakdown': breakdown,
        **billing_profile(product),
    }


# ─────────────────────────────────────────────────────────────
# 资源实际规格 → 产品计费（基础规格定价 + 超出部分阶梯计费）
# ─────────────────────────────────────────────────────────────

def spec_from_vps(vps) -> dict:
    """从 VPS 实例提取实际规格（IP 数量按已分配 IP 统计）。"""
    ip_count = vps.ip_addresses.count() if hasattr(vps, 'ip_addresses') else 0
    return {
        'cpu_cores': vps.vcpu or 0,
        'ram_mb': vps.ram_mb or 0,
        'disk_gb': vps.disk_gb or 0,
        'bandwidth_mbps': vps.bandwidth_mbps or 0,
        'ip_count': ip_count,
        'nic_count': vps.nic_count or 0,
    }


def spec_from_edge(edge) -> dict:
    """从边缘加速实例提取实际规格（不含隧道段地址）。"""
    ip_count = 0
    if hasattr(edge, 'ip_addresses'):
        qs = edge.ip_addresses.all()
        try:
            from resource_manager.ip_scope import exclude_tunnel_addresses
            ip_count = exclude_tunnel_addresses(qs).count()
        except Exception:
            ip_count = qs.count()
    return {
        'cpu_cores': 0,
        'ram_mb': 0,
        'disk_gb': 0,
        'bandwidth_mbps': edge.bandwidth_mbps or 0,
        'ip_count': ip_count,
        'nic_count': 0,
    }


def spec_from_bandwidth(alloc) -> dict:
    """从带宽分配记录提取实际规格。"""
    return {
        'cpu_cores': 0,
        'ram_mb': 0,
        'disk_gb': 0,
        'bandwidth_mbps': (alloc.allocated_mbps or 0) + (alloc.burst_mbps or 0),
        'ip_count': 0,
        'nic_count': 0,
    }


def resource_spec(resource, kind: str | None = None) -> dict:
    """根据资源类型提取实际规格。kind 缺省时按类名推断。"""
    if kind is None:
        kind = resource.__class__.__name__.lower()
    if kind in ('vps',):
        return spec_from_vps(resource)
    if kind in ('edge', 'edgeacceleration'):
        return spec_from_edge(resource)
    if kind in ('bandwidth', 'bandwidthallocation'):
        return spec_from_bandwidth(resource)
    return {}


def compute_resource_billing(resource, kind: str | None = None) -> dict | None:
    """计算某「已关联产品」的资源实例的计费明细。

    返回 None 表示该资源未关联产品（无法据产品计费）。
    否则返回：
      {
        product_id, product_no, product_name, currency,
        base_monthly,        # 产品基础月租
        overage_monthly,     # 超出模版部分的阶梯升级月租
        monthly_price,       # 合计月租 = 基础 + 超出
        reputation_price,
        spec,                # 资源实际规格
        breakdown,           # 各资源项超出明细
      }
    """
    product = getattr(resource, 'product', None)
    if product is None:
        return None
    spec = resource_spec(resource, kind)
    price = compute_price(product, spec)
    base = Decimal(str(price['base_monthly_price']))
    total = Decimal(str(price['monthly_price']))
    overage = total - base
    return {
        'product_id': product.id,
        'product_no': getattr(product, 'product_no', ''),
        'product_name': product.name,
        'currency': price['currency'],
        'base_monthly': str(base),
        'overage_monthly': str(overage),
        'monthly_price': str(total),
        'reputation_price': price['reputation_price'],
        'spec': spec,
        'base_spec': product_base_spec(product),
        'breakdown': price['breakdown'],
        'billing_mode': price.get('billing_mode'),
        'billing_mode_label': price.get('billing_mode_label'),
        'transfer_quota_gb': price.get('transfer_quota_gb'),
        'overage_unit_price': price.get('overage_unit_price'),
    }


def resolve_provision_estimate(product, *, capacity=None, pop=None, now=None) -> dict:
    """计算预计开通分钟数与交付时间。库存不足时优先用产品上填写的预估小时。"""
    from datetime import timedelta

    from django.utils import timezone

    pop = pop or getattr(product, 'pop_point', None)
    cap_ok = True if capacity is None else bool(capacity.get('ok'))
    hours = getattr(product, 'estimated_provision_hours', None)
    minutes = None
    source = None
    if (not cap_ok or getattr(product, 'allow_backorder', False)) and hours:
        minutes = int(hours) * 60
        source = 'backorder'
    elif hours:
        minutes = int(hours) * 60
        source = 'product'
    elif pop is not None:
        auto = getattr(pop, 'ConfigMode', None)
        is_auto = bool(auto and pop.config_mode == auto.AUTO)
        minutes = 15 if is_auto else 24 * 60
        source = 'pop'
    stamp = now or timezone.now()
    expected_at = stamp + timedelta(minutes=minutes) if minutes else None
    return {
        'estimated_provision_minutes': minutes,
        'estimated_provision_hours': (minutes / 60) if minutes else None,
        'is_backorder': bool(not cap_ok),
        'expected_delivery_at': expected_at,
        'source': source,
    }
