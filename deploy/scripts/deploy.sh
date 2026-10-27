#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# deploy.sh — 部署编排：迁移库 → 生成运行时环境 → systemd → nginx
#   前置：已执行 01-init-ubuntu.sh、02-setup-database.sh、build.sh
#   若证书已存在，渲染 HTTPS 站点（不会覆盖 Let's Encrypt / 自签证书）
# 用法：sudo bash deploy.sh
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env
require_root

SERVICE_FILE="/etc/systemd/system/ispmanager.service"
QUALITY_SERVICE_FILE="/etc/systemd/system/ispmanager-pop-quality.service"
EXPIRY_SERVICE_FILE="/etc/systemd/system/ispmanager-instance-expiry.service"
EXPIRY_TIMER_FILE="/etc/systemd/system/ispmanager-instance-expiry.timer"
MAINT_SERVICE_FILE="/etc/systemd/system/ispmanager-maintenance.service"
MAINT_TIMER_FILE="/etc/systemd/system/ispmanager-maintenance.timer"
DAILY_SERVICE_FILE="/etc/systemd/system/ispmanager-daily-jobs.service"
DAILY_TIMER_FILE="/etc/systemd/system/ispmanager-daily-jobs.timer"

ADMIN_DIST="$ADMIN_FRONTEND_DIR/dist"
PORTAL_DIST="$PORTAL_FRONTEND_DIR/dist"

step "1/8 前置校验"
[[ -x "$VENV_DIR/bin/python" ]] || die "未找到 venv，请先执行 build.sh"
python_version_ok "$VENV_DIR/bin/python" || die "venv 不是 Python 3.12+，请删除 $VENV_DIR 后重跑 build.sh"
require_frontend_dist "管理端" "$ADMIN_FRONTEND_DIR"
require_admin_dist_base
if layout_is_split; then
    require_split_portal_api
    log "分机部署：本机不安装客户门户页面"
else
    require_frontend_dist "客户门户" "$PORTAL_FRONTEND_DIR"
fi

if is_placeholder "${DJANGO_SECRET_KEY:-}"; then
    die "DJANGO_SECRET_KEY 仍是占位符。请编辑 deploy/.env 或运行 install.sh 自动生成"
fi
if [[ "${DJANGO_SECRET_KEY}" == django-insecure-* ]]; then
    die "DJANGO_SECRET_KEY 不能使用开发默认值"
fi
if is_placeholder "${DB_PASSWORD:-}"; then
    die "DB_PASSWORD 仍是占位符"
fi
if [[ -z "${DJANGO_ALLOWED_HOSTS:-}" || "$DJANGO_ALLOWED_HOSTS" == "*" ]]; then
    die "DJANGO_ALLOWED_HOSTS 必须是明确域名/IP，禁止 *"
fi
if [[ -z "${DJANGO_CORS_ALLOWED_ORIGINS:-}" ]]; then
    die "DJANGO_CORS_ALLOWED_ORIGINS 为空，生产环境会拒绝启动"
fi
[[ "${PAYMENT_CONFIG_ENCRYPTION_KEY:-}" == CHANGE_ME* ]] && \
    warn "PAYMENT_CONFIG_ENCRYPTION_KEY 仍是默认值，在线支付凭据不应投入生产"

normalize_publish_settings
if ssl_material_ready; then
    export DJANGO_SECURE_PROXY_SSL_HEADER=True
    export PUBLIC_PORT="${PUBLIC_PORT:-$PUBLIC_HTTPS_PORT}"
    export ADMIN_PUBLIC_PORT="$PUBLIC_PORT"
    if ! layout_is_split; then
        export PORTAL_PUBLIC_PORT="$PUBLIC_PORT"
    fi
    ensure_payment_frontend_url https
    apply_public_origins
    log "检测到已有证书，将渲染 HTTPS nginx（浏览器端口 ${PUBLIC_PORT}，监听 ${LISTEN_HTTPS_PORT}）"
else
    ensure_payment_frontend_url http
    apply_public_origins
fi
ok "校验通过"

if [[ -f "$DEPLOY_DIR/.env" ]]; then
    upsert_env_file "$DEPLOY_DIR/.env" SITE_DOMAIN "$SITE_DOMAIN"
    upsert_env_file "$DEPLOY_DIR/.env" ADMIN_DOMAIN "$SITE_DOMAIN"
    upsert_env_file "$DEPLOY_DIR/.env" DEPLOY_LAYOUT "$DEPLOY_LAYOUT"
    if layout_is_split; then
        upsert_env_file "$DEPLOY_DIR/.env" PORTAL_DOMAIN "$PORTAL_DOMAIN"
        upsert_env_file "$DEPLOY_DIR/.env" PORTAL_PUBLIC_ORIGIN "$PORTAL_PUBLIC_ORIGIN"
        upsert_env_file "$DEPLOY_DIR/.env" PORTAL_API_PORT "$PORTAL_API_PORT"
        upsert_env_file "$DEPLOY_DIR/.env" PORTAL_API_ALLOW_CIDRS "$PORTAL_API_ALLOW_CIDRS"
        upsert_env_file "$DEPLOY_DIR/.env" PORTAL_API_SECRET "$PORTAL_API_SECRET"
    else
        upsert_env_file "$DEPLOY_DIR/.env" PORTAL_DOMAIN "$SITE_DOMAIN"
    fi
    upsert_env_file "$DEPLOY_DIR/.env" ADMIN_BASE_PATH "$ADMIN_BASE_PATH"
    upsert_env_file "$DEPLOY_DIR/.env" PAYMENT_FRONTEND_URL "$PAYMENT_FRONTEND_URL"
    upsert_env_file "$DEPLOY_DIR/.env" DJANGO_CSRF_TRUSTED_ORIGINS "$DJANGO_CSRF_TRUSTED_ORIGINS"
    upsert_env_file "$DEPLOY_DIR/.env" DJANGO_CORS_ALLOWED_ORIGINS "$DJANGO_CORS_ALLOWED_ORIGINS"
fi

step "2/8 生成运行时环境文件 $RUNTIME_ENV_FILE"
write_runtime_env "$RUNTIME_ENV_FILE"
ok "已生成（权限 640，属组 $APP_GROUP）"

step "3/8 数据库迁移与静态收集"
pushd "$BACKEND_DIR" >/dev/null
source_env_file "$RUNTIME_ENV_FILE"
if ! "$VENV_DIR/bin/python" manage.py migrate --noinput --skip-checks; then
    warn "migrate --skip-checks 失败，再试标准 migrate"
    "$VENV_DIR/bin/python" manage.py migrate --noinput || \
        die "数据库迁移失败，已中止部署（避免程序新、库旧）"
fi
mkdir -p "$DJANGO_STATIC_ROOT" "$DJANGO_MEDIA_ROOT"
"$VENV_DIR/bin/python" manage.py collectstatic --noinput
[[ -f "$DJANGO_STATIC_ROOT/admin/css/base.css" ]] || \
    die "collectstatic 未生成 admin 静态文件，请检查 DJANGO_STATIC_ROOT=$DJANGO_STATIC_ROOT"
chmod -R a+rX "$DJANGO_STATIC_ROOT" 2>/dev/null || true
ok "迁移与静态收集完成（admin 静态已就绪）"

if [[ -n "${DJANGO_SUPERUSER_USERNAME:-}" && -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]] \
    && ! is_placeholder "$DJANGO_SUPERUSER_PASSWORD"; then
    step "3b/8 创建/更新 Django 超级管理员：$DJANGO_SUPERUSER_USERNAME"
    DJANGO_SUPERUSER_PASSWORD="$DJANGO_SUPERUSER_PASSWORD" \
        "$VENV_DIR/bin/python" manage.py createsuperuser \
        --noinput --username "$DJANGO_SUPERUSER_USERNAME" \
        --email "${DJANGO_SUPERUSER_EMAIL:-admin@example.com}" 2>/dev/null && \
        ok "超级管理员已创建" || \
        log "超级管理员已存在（跳过创建）"
fi
popd >/dev/null

step "4/8 设置目录属主与网页升级权限"
prepare_web_upgrade_permissions
chmod -R a+rX "$ADMIN_DIST" "$DJANGO_STATIC_ROOT" "$DJANGO_MEDIA_ROOT" 2>/dev/null || true
if [[ -d "$PORTAL_DIST" ]]; then
    chmod -R a+rX "$PORTAL_DIST" 2>/dev/null || true
fi
ensure_nginx_traverse "$ADMIN_DIST" "$DJANGO_STATIC_ROOT" "$DJANGO_MEDIA_ROOT"
[[ -d "$PORTAL_DIST" ]] && ensure_nginx_traverse "$PORTAL_DIST" || true
ok "属主/权限设置完成（后续版本可走管理端网页升级）"

step "5/8 安装 systemd 服务"
render_systemd_service() {
    local template="$1" target="$2"
    sed -e "s|__APP_USER__|$APP_USER|g" \
        -e "s|__APP_GROUP__|$APP_GROUP|g" \
        -e "s|__BACKEND_DIR__|$BACKEND_DIR|g" \
        -e "s|__VENV_DIR__|$VENV_DIR|g" \
        -e "s|__DEPLOY_DIR__|$DEPLOY_DIR|g" \
        -e "s|__RUNTIME_ENV_FILE__|$RUNTIME_ENV_FILE|g" \
        "$template" > "$target"
}

render_systemd_service \
    "$DEPLOY_DIR/systemd/ispmanager.service.template" \
    "$SERVICE_FILE"
render_systemd_service \
    "$DEPLOY_DIR/systemd/ispmanager-pop-quality.service.template" \
    "$QUALITY_SERVICE_FILE"
render_systemd_service \
    "$DEPLOY_DIR/systemd/ispmanager-instance-expiry.service.template" \
    "$EXPIRY_SERVICE_FILE"
render_systemd_service \
    "$DEPLOY_DIR/systemd/ispmanager-instance-expiry.timer.template" \
    "$EXPIRY_TIMER_FILE"
render_systemd_service \
    "$DEPLOY_DIR/systemd/ispmanager-maintenance.service.template" \
    "$MAINT_SERVICE_FILE"
render_systemd_service \
    "$DEPLOY_DIR/systemd/ispmanager-maintenance.timer.template" \
    "$MAINT_TIMER_FILE"
render_systemd_service \
    "$DEPLOY_DIR/systemd/ispmanager-daily-jobs.service.template" \
    "$DAILY_SERVICE_FILE"
render_systemd_service \
    "$DEPLOY_DIR/systemd/ispmanager-daily-jobs.timer.template" \
    "$DAILY_TIMER_FILE"
systemctl daemon-reload
systemctl enable ispmanager.service ispmanager-pop-quality.service \
    ispmanager-instance-expiry.timer \
    ispmanager-maintenance.timer \
    ispmanager-daily-jobs.timer >/dev/null 2>&1 || true
systemctl restart ispmanager.service
systemctl restart ispmanager-pop-quality.service
systemctl start ispmanager-instance-expiry.timer >/dev/null 2>&1 || true
systemctl start ispmanager-maintenance.timer >/dev/null 2>&1 || true
systemctl start ispmanager-daily-jobs.timer >/dev/null 2>&1 || true
sleep 2
if systemctl is-active --quiet ispmanager.service && \
    systemctl is-active --quiet ispmanager-pop-quality.service; then
    ok "后端与 POP 品质监控已启动；到期/维护/日终 timer 已启用"
else
    err "服务启动失败，查看日志：journalctl -u ispmanager -n 50 / journalctl -u ispmanager-pop-quality -n 50"
    journalctl -u ispmanager -n 20 --no-pager || true
    journalctl -u ispmanager-pop-quality -n 20 --no-pager || true
    die "部署中断"
fi

step "6/8 安装 nginx 站点配置"
ensure_listen_firewall
render_nginx_conf "$NGINX_AVAIL"
render_portal_api_nginx
enable_nginx_site

step "7/8 校验并重载 nginx"
reload_nginx
ok "nginx 配置生效"

step "8/8 权限与日志目录同步"
run_post_upgrade_hooks || warn "升级后置命令未完全成功"

step "9/9 部署完成"
SCHEME="http"
if ssl_material_ready; then
    SCHEME="https"
fi
echo
ok "════════════════════════════════════════════════════"
ok " 部署成功"
ok "════════════════════════════════════════════════════"
print_site_urls "$SCHEME"
echo -e "  API 文档： ${SCHEME}://${SITE_DOMAIN}/api/docs/"
echo
if ssl_material_ready; then
    echo -e "  HTTPS 已启用。证书续期：sudo certbot renew（Let's Encrypt）"
else
    echo -e "  下一步（启用 HTTPS）： sudo bash setup-ssl.sh"
fi
echo -e "  服务管理：            bash manage-service.sh {status|restart|logs}"
