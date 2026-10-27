from __future__ import annotations

import json
import logging

from .services import (
    action_type_label,
    infer_action_from_request,
    infer_module_from_path,
    is_request_logged,
    parse_api_target,
    record_audit_log,
    reset_request_logged,
    sanitize_payload,
)

logger = logging.getLogger(__name__)

WRITE_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

SKIP_PREFIXES = (
    '/api/log/',
    '/api/schema/',
    '/api/docs/',
    '/api/redoc/',
    '/admin/',
    '/static/',
    '/media/',
)

SKIP_SUBSTRINGS = (
    '/token/refresh',
    '/token/verify',
    '/tokens/verify',
    '/edge-quality/ingest',
    '/telemetry',
)


class AuditLogMiddleware:
    """补记未走专用 record_* 的写接口（2xx）。已由信号/显式记录的请求会跳过。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        reset_request_logged()
        self._cache_body(request)
        response = self.get_response(request)
        try:
            self._maybe_log(request, response)
        except Exception:
            logger.exception('审计中间件写入失败')
        return response

    def _cache_body(self, request) -> None:
        if (request.method or '').upper() not in WRITE_METHODS:
            setattr(request, '_audit_body', None)
            return
        try:
            setattr(request, '_audit_body', request.body)
        except Exception:
            setattr(request, '_audit_body', getattr(request, '_body', None))

    def _maybe_log(self, request, response) -> None:
        method = (request.method or '').upper()
        if method not in WRITE_METHODS:
            return
        if not (200 <= getattr(response, 'status_code', 0) < 300):
            return
        path = request.path or ''
        if any(path.startswith(prefix) for prefix in SKIP_PREFIXES):
            return
        if any(token in path for token in SKIP_SUBSTRINGS):
            return
        if is_request_logged(request):
            return

        action_type = infer_action_from_request(method, path)
        module = infer_module_from_path(path)
        target_model, target_id = parse_api_target(path)
        body = self._request_body(request)
        description = f'{action_type_label(action_type)} {path}'
        record_audit_log(
            module=module,
            action_type=action_type,
            description=description,
            request=request,
            target_model=target_model,
            target_id=target_id,
            target_repr=target_id and f'{target_model}#{target_id}' or target_model,
            detail={'request': body} if body not in (None, {}, []) else {},
            source='middleware',
        )

    def _request_body(self, request):
        raw = getattr(request, '_audit_body', None)
        if raw is None:
            raw = getattr(request, '_body', None)
        if raw:
            if len(raw) > 8000:
                return None
            try:
                return sanitize_payload(json.loads(raw.decode('utf-8')))
            except Exception:
                return None
        post = getattr(request, 'POST', None)
        if post:
            try:
                return sanitize_payload(post.dict())
            except Exception:
                return None
        return None
