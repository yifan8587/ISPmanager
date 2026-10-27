"""边缘加速类型：仅 GRE / WireGuard，可多选。"""

from __future__ import annotations

ACCEL_GRE = 'gre'
ACCEL_WIREGUARD = 'wireguard'
ALLOWED_ACCEL_TYPES = (ACCEL_GRE, ACCEL_WIREGUARD)
ACCEL_LABELS = {
    ACCEL_GRE: 'GRE',
    ACCEL_WIREGUARD: 'WireGuard',
}


def parse_accel_types(raw) -> list[str]:
    if raw is None or raw == '':
        return []
    if isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        items = str(raw).replace(';', ',').replace('/', ',').split(',')
    out: list[str] = []
    for item in items:
        value = str(item or '').strip().lower()
        if value in ('wg', 'wireguard'):
            value = ACCEL_WIREGUARD
        elif value in ('gre',):
            value = ACCEL_GRE
        elif value in ('proxy', 'socks5', 'socks'):
            continue
        if value in ALLOWED_ACCEL_TYPES and value not in out:
            out.append(value)
    return out


def join_accel_types(raw) -> str:
    return ','.join(parse_accel_types(raw))


def accel_types_label(raw) -> str:
    types = parse_accel_types(raw)
    if not types:
        return '-'
    return ' / '.join(ACCEL_LABELS[item] for item in types)


def default_edge_business_type(raw) -> str:
    """下单时的默认业务类型；多选时先按 GRE 建单，客户开通后可改对接模式。"""
    types = parse_accel_types(raw)
    if types == [ACCEL_WIREGUARD]:
        return 'edge_wg'
    return 'edge_gre'
