"""生产环境 CSRF / CORS 可信来源展开。

常见失败场景：
- nginx 用 $host（不含端口）做 X-Forwarded-Host，浏览器 Origin 却是
  http://www.example.com:30090
- PUBLIC_PORT 配成 80/443，实际经面板/Docker 映射到其它端口
- 反代误传 X-Forwarded-Proto=https，但用户仍用 http:// 打开
"""
from __future__ import annotations

from urllib.parse import urlsplit


def _clean_host(raw: str) -> str:
    host = (raw or '').strip().rstrip('/')
    if not host or host == '*':
        return ''
    if '://' in host:
        host = host.split('://', 1)[1]
    host = host.split('/', 1)[0].strip()
    if host.startswith('[') and ']' in host:
        return host
    return host


def iter_hosts(*groups) -> list[str]:
    seen: list[str] = []
    for group in groups:
        if group is None:
            continue
        if isinstance(group, str):
            parts = group.replace(';', ',').replace(' ', ',').split(',')
        else:
            parts = list(group)
        for item in parts:
            host = _clean_host(str(item))
            if not host or host.startswith('.'):
                continue
            if host not in seen:
                seen.append(host)
    return seen


def collect_public_ports(*values) -> list[str]:
    ports: list[str] = []
    for value in values:
        if value is None or value == '':
            continue
        if isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = str(value).replace(';', ',').split(',')
        for item in items:
            port = str(item).strip()
            if port.isdigit() and port not in ports:
                ports.append(port)
    return ports


def hostname_of(value: str) -> str:
    """从 Origin URL 或 Host 头取出主机名（去掉端口）。"""
    text = (value or '').strip()
    if not text:
        return ''
    if '://' in text:
        try:
            parsed = urlsplit(text)
        except ValueError:
            return ''
        return (parsed.hostname or '').lower()
    if text.startswith('['):
        end = text.find(']')
        if end != -1:
            return text[1:end].lower()
    return text.split('/')[0].split(':')[0].lower()


def origin_for(scheme: str, host: str, port: str | int | None = None) -> str:
    port_s = '' if port is None else str(port).strip()
    if not port_s or (scheme == 'https' and port_s == '443') or (scheme == 'http' and port_s == '80'):
        return f'{scheme}://{host}'
    if ':' in host and not host.startswith('['):
        return f'{scheme}://{host}'
    return f'{scheme}://{host}:{port_s}'


def expand_trusted_origins(
    *,
    explicit: list[str] | None = None,
    hosts: list[str] | None = None,
    public_port: str | int | None = None,
    public_ports: list[str] | None = None,
) -> list[str]:
    origins: list[str] = []
    for item in explicit or []:
        value = (item or '').strip().rstrip('/')
        if value and value not in origins:
            origins.append(value)

    ports = collect_public_ports(public_port, public_ports)
    pairs = [('https', '443'), ('http', '80')]
    for port in ports:
        if port not in ('80', '443'):
            pairs.extend([('https', port), ('http', port)])
    for host in hosts or []:
        for scheme, candidate_port in pairs:
            origin = origin_for(scheme, host, candidate_port)
            if origin not in origins:
                origins.append(origin)
    return origins


def origin_matches_request_host(origin_or_url: str, request) -> bool:
    """同一主机名即视为同站，忽略映射端口和 http/https 差异。

    攻击站 Origin 是 evil.com，请求 Host 是业务域名，不会通过。
    """
    text = (origin_or_url or '').strip()
    if not text:
        return False
    if '://' in text:
        try:
            parsed = urlsplit(text)
        except ValueError:
            return False
        if parsed.scheme not in ('http', 'https'):
            return False
    origin_host = hostname_of(text)
    if not origin_host:
        return False

    raw_host = ''
    try:
        raw_host = request.get_host()
    except Exception:
        raw_host = (
            request.META.get('HTTP_X_FORWARDED_HOST')
            or request.META.get('HTTP_HOST')
            or ''
        )
    request_host = hostname_of(raw_host)
    return bool(request_host) and origin_host == request_host
