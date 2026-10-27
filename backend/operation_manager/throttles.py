"""运维公开接收端的限流策略。"""
from __future__ import annotations

import hashlib

from rest_framework.throttling import SimpleRateThrottle


class EdgeTelemetryIngestRateThrottle(SimpleRateThrottle):
    """按 POP 遥测令牌（缺失时按源 IP）限制公开 ingest 入口。"""

    scope = 'edge_telemetry_ingest'

    def get_cache_key(self, request, view):
        token = (request.headers.get('X-POP-Telemetry-Token') or '').strip()
        if token:
            # 缓存键中仅存摘要，避免令牌出现在缓存诊断或指标中。
            ident = f'token:{hashlib.sha256(token.encode("utf-8")).hexdigest()}'
        else:
            ident = f'ip:{self.get_ident(request)}'
        return self.cache_format % {'scope': self.scope, 'ident': ident}
