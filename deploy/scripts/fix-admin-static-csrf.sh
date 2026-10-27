#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# fix-admin-static-csrf.sh — 修复 Django Admin 样式丢失 + CSRF 403
# 用法：sudo bash fix-admin-static-csrf.sh
# 已启用 HTTPS 时会保持 443 站点，不会退回纯 HTTP。
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env
require_root

RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-/etc/ispmanager.env}"
ADMIN_CSS="$DJANGO_STATIC_ROOT/admin/css/base.css"
ORIGIN="$(origin_url https "$SITE_DOMAIN" "${PUBLIC_PORT:-443}")"
if ! ssl_material_ready; then
    ORIGIN="$(origin_url http "$SITE_DOMAIN" "${PUBLIC_PORT:-${ADMIN_PUBLIC_PORT:-80}}")"
fi
# 映射端口（如 :30090）也写入可信来源
apply_public_origins
upsert_env_file "$DEPLOY_DIR/.env" DJANGO_CSRF_TRUSTED_ORIGINS "${DJANGO_CSRF_TRUSTED_ORIGINS:-}"
upsert_env_file "$DEPLOY_DIR/.env" DJANGO_CORS_ALLOWED_ORIGINS "${DJANGO_CORS_ALLOWED_ORIGINS:-}"

step "1/5 检查配置"
log "SITE_DOMAIN           = $SITE_DOMAIN"
log "ADMIN_BASE_PATH       = $ADMIN_BASE_PATH"
log "PUBLIC_PORT           = ${PUBLIC_PORT}"
log "DJANGO_STATIC_ROOT    = $DJANGO_STATIC_ROOT"
log "CSRF_TRUSTED_ORIGINS  = $DJANGO_CSRF_TRUSTED_ORIGINS"
log "期望浏览器 Origin     = $ORIGIN"

[[ -n "${DJANGO_CSRF_TRUSTED_ORIGINS:-}" ]] || \
    die "CSRF 可信来源仍为空，请检查 deploy/.env 中 SITE_DOMAIN / PUBLIC_PORT"
[[ -x "$VENV_DIR/bin/python" ]] || die "未找到 venv：$VENV_DIR，请先 bash build.sh"

step "2/5 重新收集 Django 静态资源"
mkdir -p "$DJANGO_STATIC_ROOT" "$DJANGO_MEDIA_ROOT"
pushd "$BACKEND_DIR" >/dev/null
export DJANGO_STATIC_ROOT DJANGO_MEDIA_ROOT
"$VENV_DIR/bin/python" manage.py collectstatic --noinput --clear
popd >/dev/null
[[ -f "$ADMIN_CSS" ]] || die "collectstatic 后仍缺少 $ADMIN_CSS"
chmod -R a+rX "$DJANGO_STATIC_ROOT" "$DJANGO_MEDIA_ROOT" 2>/dev/null || true
ensure_nginx_traverse "$DJANGO_STATIC_ROOT" "$ADMIN_FRONTEND_DIR/dist" "$PORTAL_FRONTEND_DIR/dist"
ok "admin 静态文件已就绪：$ADMIN_CSS"

step "3/5 更新运行时环境"
write_runtime_env "$RUNTIME_ENV_FILE"
ok "已写入 $RUNTIME_ENV_FILE"

step "4/5 重新渲染 nginx（保留已有证书）"
render_nginx_conf "$NGINX_AVAIL"
enable_nginx_site
systemctl restart ispmanager.service
sleep 2
systemctl is-active --quiet ispmanager.service || {
    journalctl -u ispmanager -n 20 --no-pager || true
    die "后端启动失败"
}
reload_nginx
ok "后端已重启，nginx 已重载"

step "5/5 自检"
if [[ ",${DJANGO_CSRF_TRUSTED_ORIGINS}," == *",${ORIGIN},"* ]]; then
    ok "CSRF 可信来源已包含 $ORIGIN"
else
    warn "CSRF 未包含 $ORIGIN，当前：$DJANGO_CSRF_TRUSTED_ORIGINS"
fi

STATIC_URL_CHECK="$("$VENV_DIR/bin/python" -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','backend.settings')
import django; django.setup()
from django.conf import settings
print(settings.STATIC_URL)
" 2>/dev/null || echo "unknown")"
if [[ "$STATIC_URL_CHECK" == "/static/" ]]; then
    ok "STATIC_URL = /static/"
else
    warn "STATIC_URL = $STATIC_URL_CHECK（须为 /static/）"
fi

echo
ok "修复完成  Admin：${ORIGIN}/admin/  静态：${ORIGIN}/static/admin/css/base.css"
