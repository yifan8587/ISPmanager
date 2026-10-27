#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# verify-install.sh — 部署后自检（nginx / 后端 / 前后端入口 / 静态）
# 用法：sudo bash verify-install.sh
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env

fail=0
check() {
    local title="$1"
    shift
    if "$@"; then
        ok "$title"
    else
        err "$title"
        fail=1
    fi
}

http_code() {
    curl -fsS -o /dev/null -w '%{http_code}' --max-time 15 "$@"
}

step "服务进程"
if has_systemd; then
    check "ispmanager.service 运行中" systemctl is-active --quiet ispmanager.service
    check "nginx 运行中" systemctl is-active --quiet nginx
else
    check "nginx 可执行" command -v nginx
fi

step "本机后端"
code="$(http_code "http://${APP_BIND}/api/" || echo 000)"
if [[ "$code" =~ ^(200|301|302|401|403)$ ]]; then
    ok "gunicorn 本机探测 HTTP $code （$APP_BIND/api/）"
else
    err "gunicorn 本机探测失败 HTTP $code"
    fail=1
fi

SCHEME=http
CURL_EXTRA=()
if ssl_material_ready; then
    SCHEME=https
    CURL_EXTRA=(-k)
fi

LISTEN_PROBE_PORT=80
if [[ "$SCHEME" == "https" ]]; then
    LISTEN_PROBE_PORT="${LISTEN_HTTPS_PORT:-443}"
else
    LISTEN_PROBE_PORT="${LISTEN_HTTP_PORT:-80}"
fi

probe() {
    local host="$1" path="$2" expect="$3" title="$4"
    local args=(-sS -o /dev/null -w '%{http_code}' --max-time 15 -H "Host: ${host}")
    if [[ "$SCHEME" == "https" ]]; then
        args+=(-k --resolve "${host}:${LISTEN_PROBE_PORT}:127.0.0.1" "https://${host}:${LISTEN_PROBE_PORT}${path}")
    else
        args+=(--resolve "${host}:${LISTEN_PROBE_PORT}:127.0.0.1" "http://${host}:${LISTEN_PROBE_PORT}${path}")
    fi
    local got
    got="$(curl "${args[@]}" || echo 000)"
    if [[ ",${expect}," == *",${got},"* ]]; then
        ok "$title  HTTP $got"
    else
        err "$title  HTTP $got（期望 ${expect}）"
        fail=1
    fi
}

step "HTTPS/HTTP 入口"
if layout_is_split; then
    probe "$SITE_DOMAIN" "/" "301,302" "管理端入口跳转"
else
    probe "$SITE_DOMAIN" "/" "200" "客户门户 SPA"
fi
probe "$SITE_DOMAIN" "${ADMIN_BASE_PATH}" "301,302" "管理端 /manager 补斜杠"
probe "$SITE_DOMAIN" "${ADMIN_BASE_PATH}/" "200" "管理端 SPA"
probe "$SITE_DOMAIN" "/admin/login/" "200,302" "Django Admin"
probe "$SITE_DOMAIN" "/static/admin/css/base.css" "200" "Admin 静态 CSS"
probe "$SITE_DOMAIN" "/api/" "200,401,403" "API"

if [[ -f "$DJANGO_STATIC_ROOT/admin/css/base.css" ]]; then
    ok "collectstatic 文件存在"
else
    err "缺少 $DJANGO_STATIC_ROOT/admin/css/base.css"
    fail=1
fi
if layout_is_split; then
    if [[ -f "$ADMIN_FRONTEND_DIR/dist/index.html" ]]; then
        ok "管理端 dist 存在（门户在另一台服务器）"
    else
        err "管理端 dist 缺失"
        fail=1
    fi
elif [[ -f "$ADMIN_FRONTEND_DIR/dist/index.html" && -f "$PORTAL_FRONTEND_DIR/dist/index.html" ]]; then
    ok "两个前端 dist 存在"
else
    err "前端 dist 缺失"
    fail=1
fi
if grep -F -q "${ADMIN_BASE_PATH}/" "$ADMIN_FRONTEND_DIR/dist/index.html"; then
    ok "管理端 dist 基路径 ${ADMIN_BASE_PATH}/"
else
    err "管理端 dist 未包含基路径 ${ADMIN_BASE_PATH}/，静态资源会 404"
    fail=1
fi

step "网页升级权限"
if id "${APP_USER}" >/dev/null 2>&1; then
    portal_writable=1
    if ! layout_is_split && ! sudo -u "$APP_USER" test -w "$PORTAL_FRONTEND_DIR"; then
        portal_writable=0
    fi
    if sudo -u "$APP_USER" test -w "$ISPMANAGER_DIR/VERSION" \
        && sudo -u "$APP_USER" test -w "$ADMIN_FRONTEND_DIR" \
        && [[ "$portal_writable" == "1" ]]; then
        ok "服务用户可写 VERSION 与需要升级的前端目录"
    else
        err "服务用户无法写入升级目录，请执行 prepare-web-upgrade.sh"
        fail=1
    fi
    if [[ -f /etc/sudoers.d/ispmanager-web-upgrade ]]; then
        ok "网页升级 sudoers 已安装"
    else
        warn "未安装 /etc/sudoers.d/ispmanager-web-upgrade，管理端升级后需手工重启"
    fi
else
    warn "系统用户 ${APP_USER} 不存在，跳过网页升级权限检查"
fi

step "Django Admin / CSRF"
if [[ -n "${DJANGO_CSRF_TRUSTED_ORIGINS:-}" ]]; then
    ok "CSRF 可信来源已配置"
else
    err "DJANGO_CSRF_TRUSTED_ORIGINS 为空，/admin/ 登录可能 403"
    fail=1
fi
admin_html="$(mktemp)"
if [[ "$SCHEME" == "https" ]]; then
    curl -fsSk --max-time 15 --resolve "${SITE_DOMAIN}:${LISTEN_PROBE_PORT}:127.0.0.1" \
        "https://${SITE_DOMAIN}:${LISTEN_PROBE_PORT}/admin/login/" -o "$admin_html" || true
else
    curl -fsS --max-time 15 --resolve "${SITE_DOMAIN}:${LISTEN_PROBE_PORT}:127.0.0.1" \
        "http://${SITE_DOMAIN}:${LISTEN_PROBE_PORT}/admin/login/" -o "$admin_html" || true
fi
if grep -q 'csrfmiddlewaretoken' "$admin_html" 2>/dev/null; then
    ok "Django Admin 登录页含 CSRF token"
else
    err "Django Admin 登录页未输出 CSRF token"
    fail=1
fi
rm -f "$admin_html"

echo
if [[ "$fail" -eq 0 ]]; then
    ok "自检通过：${SCHEME}://${SITE_DOMAIN}/  与  ${SCHEME}://${SITE_DOMAIN}${ADMIN_BASE_PATH}"
    exit 0
fi
err "自检存在失败项，请查看上方输出与 journalctl -u ispmanager -n 50"
exit 1
