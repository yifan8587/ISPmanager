#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# install.sh — Ubuntu 22.04 / 24.04 + nginx 一键安装部署
#   不修改业务仓库源码，只使用本目录部署脚本。
#
# 用法（发布包解压后）：
#   sudo SITE_DOMAIN=www.example.net \
#        CERTBOT_EMAIL=ops@example.net \
#        bash install.sh
#
#   sudo SSL_MODE=selfsigned SITE_DOMAIN=www.example.net bash install.sh
#   sudo SSL_MODE=skip bash install.sh          # 仅 HTTP
#   sudo bash install.sh --check                # 仅做安装前检查
#   sudo bash install.sh                        # 已有部署时自动走升级
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
require_root

INSTALL_ARGS=("$@")
for arg in "${INSTALL_ARGS[@]}"; do
    case "$arg" in
        --check|-c)
            PREFLIGHT_SOURCED=0 bash "$SCRIPT_DIR/preflight.sh"
            exit $?
            ;;
        --upgrade)
            export FORCE_UPGRADE=1
            ;;
        --fresh)
            export FORCE_FRESH=1
            ;;
    esac
done

CRED_FILE="/root/ispmanager-credentials.txt"
ENV_FILE="$DEPLOY_DIR/.env"
EXAMPLE_FILE="$DEPLOY_DIR/.env.example"

prompt_value() {
    local prompt="$1" default="$2" result=""
    if [[ -t 0 && "${NONINTERACTIVE:-0}" != "1" ]]; then
        read -r -p "$prompt [${default}]: " result
        echo "${result:-$default}"
    else
        echo "$default"
    fi
}

prepare_env() {
    local cli_site="${SITE_DOMAIN:-}"
    local cli_admin="${ADMIN_DOMAIN:-}"
    local cli_portal="${PORTAL_DOMAIN:-}"
    local cli_layout="${DEPLOY_LAYOUT:-}"
    local cli_portal_cidrs="${PORTAL_API_ALLOW_CIDRS:-}"
    local cli_portal_port="${PORTAL_API_PORT:-}"
    local cli_portal_secret="${PORTAL_API_SECRET:-}"
    local cli_email="${CERTBOT_EMAIL:-}"
    local cli_ssl="${SSL_MODE:-}"
    local cli_db_pass="${DB_PASSWORD:-}"
    local cli_secret="${DJANGO_SECRET_KEY:-}"
    local cli_base="${ADMIN_BASE_PATH:-}"

    if [[ ! -f "$ENV_FILE" ]]; then
        [[ -f "$EXAMPLE_FILE" ]] || die "缺少 $EXAMPLE_FILE"
        cp "$EXAMPLE_FILE" "$ENV_FILE"
        ok "已从 .env.example 生成 $ENV_FILE"
    fi
    # 升级（包括换目录重新安装）先找回已在运行的密钥与分机配置，避免下面重新生成门户密钥。
    if [[ "${INSTALL_MODE:-fresh}" == "upgrade" ]]; then
        import_runtime_secrets_into_env
    fi

    source_env_file "$ENV_FILE"

    [[ -n "$cli_site" ]] && SITE_DOMAIN="$cli_site"
    [[ -n "$cli_admin" ]] && ADMIN_DOMAIN="$cli_admin"
    [[ -n "$cli_portal" ]] && PORTAL_DOMAIN="$cli_portal"
    [[ -n "$cli_layout" ]] && DEPLOY_LAYOUT="$cli_layout"
    [[ -n "$cli_portal_cidrs" ]] && PORTAL_API_ALLOW_CIDRS="$cli_portal_cidrs"
    [[ -n "$cli_portal_port" ]] && PORTAL_API_PORT="$cli_portal_port"
    [[ -n "$cli_portal_secret" ]] && PORTAL_API_SECRET="$cli_portal_secret"
    [[ -n "$cli_email" ]] && CERTBOT_EMAIL="$cli_email"
    [[ -n "$cli_ssl" ]] && SSL_MODE="$cli_ssl"
    [[ -n "$cli_db_pass" ]] && DB_PASSWORD="$cli_db_pass"
    [[ -n "$cli_secret" ]] && DJANGO_SECRET_KEY="$cli_secret"
    [[ -n "$cli_base" ]] && ADMIN_BASE_PATH="$cli_base"

    CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
    SSL_MODE="${SSL_MODE:-auto}"
    ADMIN_BASE_PATH="${ADMIN_BASE_PATH:-/manager}"
    normalize_site_settings

    if [[ "$SITE_DOMAIN" == *example.com ]]; then
        if layout_is_split; then
            SITE_DOMAIN="$(prompt_value "管理端域名" "$SITE_DOMAIN")"
        else
            SITE_DOMAIN="$(prompt_value "站点域名（门户 /、管理端 /manager、后台 /admin/）" "$SITE_DOMAIN")"
        fi
        CERTBOT_EMAIL="$(prompt_value "Let's Encrypt 通知邮箱（可空）" "${CERTBOT_EMAIL:-}")"
        export SITE_DOMAIN
        normalize_site_settings
    fi
    if layout_is_split; then
        if [[ -z "${PORTAL_DOMAIN:-}" || "$PORTAL_DOMAIN" == *example.com || "$PORTAL_DOMAIN" == "$SITE_DOMAIN" ]]; then
            PORTAL_DOMAIN="$(prompt_value "门户域名（另一台 ISPweb 服务器）" "${PORTAL_DOMAIN:-portal.example.com}")"
            export PORTAL_DOMAIN
        fi
        if [[ -z "${PORTAL_API_ALLOW_CIDRS:-}" ]]; then
            PORTAL_API_ALLOW_CIDRS="$(prompt_value "门户服务器出口 IP 或网段" "")"
            export PORTAL_API_ALLOW_CIDRS
        fi
        if [[ "${#PORTAL_API_SECRET}" -lt 32 ]]; then
            PORTAL_API_SECRET="$(gen_secret)"
            export PORTAL_API_SECRET
            log "已生成门户 API 密钥，安装结束后写入凭据文件"
        fi
        normalize_site_settings
    fi

    if [[ "${INSTALL_MODE:-fresh}" == "upgrade" ]]; then
        import_runtime_secrets_into_env
        merge_env_from_example >/dev/null || true
        source_env_file "$ENV_FILE"
        normalize_site_settings
        log "升级模式：保留已有密钥、数据库密码与站点域名"
    fi

    if is_placeholder "${DJANGO_SECRET_KEY:-}"; then
        if [[ "${INSTALL_MODE:-fresh}" == "upgrade" ]]; then
            die "升级失败：找不到有效 DJANGO_SECRET_KEY（请检查 deploy/.env 或 /etc/ispmanager.env）"
        fi
        DJANGO_SECRET_KEY="$(gen_secret)"
    fi
    if is_placeholder "${PAYMENT_CONFIG_ENCRYPTION_KEY:-}"; then
        PAYMENT_CONFIG_ENCRYPTION_KEY="$(gen_secret)"
    fi
    if is_placeholder "${DB_PASSWORD:-}"; then
        if [[ "${INSTALL_MODE:-fresh}" == "upgrade" ]]; then
            die "升级失败：找不到有效 DB_PASSWORD，拒绝改写数据库账号"
        fi
        DB_PASSWORD="$(gen_password)"
    fi
    if is_placeholder "${MYSQL_ROOT_PASSWORD:-}"; then
        MYSQL_ROOT_PASSWORD=""
    fi
    if is_placeholder "${DJANGO_SUPERUSER_PASSWORD:-}"; then
        if [[ "${INSTALL_MODE:-fresh}" == "upgrade" ]]; then
            DJANGO_SUPERUSER_PASSWORD=""
            log "升级模式：不重置超级管理员密码"
        else
            DJANGO_SUPERUSER_PASSWORD="$(gen_password)"
        fi
    fi
    DJANGO_SUPERUSER_USERNAME="${DJANGO_SUPERUSER_USERNAME:-admin}"
    DJANGO_SUPERUSER_EMAIL="${DJANGO_SUPERUSER_EMAIL:-admin@${SITE_DOMAIN}}"

    local hosts extras ip
    hosts="${SITE_DOMAIN},127.0.0.1,localhost"
    extras="$(hostname -I 2>/dev/null || true)"
    for ip in $extras; do
        [[ "$ip" == *:* ]] && continue
        hosts="$(merge_csv_unique "${hosts},${ip}")"
    done
    if ip="$(detect_public_ip || true)"; then
        hosts="$(merge_csv_unique "${hosts},${ip}")"
        PUBLIC_IP="${PUBLIC_IP:-$ip}"
    fi
    normalize_publish_settings
    if [[ "${BEHIND_NAT:-0}" == "1" ]]; then
        log "检测到内网地址，按 NAT 发布：监听 ${LISTEN_HTTP_PORT}/${LISTEN_HTTPS_PORT}，浏览器端口 ${PUBLIC_HTTP_PORT}/${PUBLIC_HTTPS_PORT}"
    fi
    if [[ -n "${SITE_ALIASES:-}" ]]; then
        local alias
        for alias in $SITE_ALIASES; do
            hosts="$(merge_csv_unique "${hosts},${alias}")"
        done
    fi
    if [[ -z "${DJANGO_ALLOWED_HOSTS:-}" || "$DJANGO_ALLOWED_HOSTS" == "*" || "$DJANGO_ALLOWED_HOSTS" == *example.com* ]]; then
        DJANGO_ALLOWED_HOSTS="$hosts"
    else
        DJANGO_ALLOWED_HOSTS="$(merge_csv_unique "${DJANGO_ALLOWED_HOSTS},${hosts}")"
    fi

    DJANGO_DEBUG=False
    DJANGO_PRODUCTION=True
    # nginx 始终传 X-Forwarded-Proto；HTTP 站点该值为 http，不会误判 HTTPS
    DJANGO_SECURE_PROXY_SSL_HEADER=True
    normalize_publish_settings
    if [[ "$SSL_MODE" == "skip" ]]; then
        PUBLIC_PORT="${PUBLIC_PORT:-$PUBLIC_HTTP_PORT}"
        ADMIN_PUBLIC_PORT="$PUBLIC_PORT"
        if ! layout_is_split; then
            PORTAL_PUBLIC_PORT="$PUBLIC_PORT"
        fi
        ensure_payment_frontend_url http
    else
        PUBLIC_PORT="${PUBLIC_PORT:-$PUBLIC_HTTPS_PORT}"
        ADMIN_PUBLIC_PORT="$PUBLIC_PORT"
        if ! layout_is_split; then
            PORTAL_PUBLIC_PORT="$PUBLIC_PORT"
        fi
        ensure_payment_frontend_url https
    fi

    export SITE_DOMAIN ADMIN_DOMAIN PORTAL_DOMAIN ADMIN_BASE_PATH
    export CERTBOT_EMAIL SSL_MODE
    export DJANGO_SECRET_KEY PAYMENT_CONFIG_ENCRYPTION_KEY
    export DB_PASSWORD MYSQL_ROOT_PASSWORD
    export DJANGO_SUPERUSER_USERNAME DJANGO_SUPERUSER_PASSWORD DJANGO_SUPERUSER_EMAIL
    export DJANGO_ALLOWED_HOSTS DJANGO_DEBUG DJANGO_PRODUCTION
    export PUBLIC_PORT ADMIN_PUBLIC_PORT PORTAL_PUBLIC_PORT
    export PUBLIC_HTTP_PORT PUBLIC_HTTPS_PORT LISTEN_HTTP_PORT LISTEN_HTTPS_PORT
    export BEHIND_NAT PUBLIC_IP SSL_HTTP_REDIRECT
    export DJANGO_SECURE_PROXY_SSL_HEADER PAYMENT_FRONTEND_URL
    apply_public_origins

    upsert_env_file "$ENV_FILE" SITE_DOMAIN "$SITE_DOMAIN"
    upsert_env_file "$ENV_FILE" ADMIN_BASE_PATH "$ADMIN_BASE_PATH"
    upsert_env_file "$ENV_FILE" ADMIN_DOMAIN "$SITE_DOMAIN"
    upsert_env_file "$ENV_FILE" DEPLOY_LAYOUT "${DEPLOY_LAYOUT:-combined}"
    if layout_is_split; then
        upsert_env_file "$ENV_FILE" PORTAL_DOMAIN "$PORTAL_DOMAIN"
        upsert_env_file "$ENV_FILE" PORTAL_PUBLIC_ORIGIN "$PORTAL_PUBLIC_ORIGIN"
        upsert_env_file "$ENV_FILE" PORTAL_API_PORT "${PORTAL_API_PORT:-8443}"
        upsert_env_file "$ENV_FILE" PORTAL_API_ALLOW_CIDRS "$PORTAL_API_ALLOW_CIDRS"
        upsert_env_file "$ENV_FILE" PORTAL_API_SECRET "$PORTAL_API_SECRET"
    else
        upsert_env_file "$ENV_FILE" PORTAL_DOMAIN "$SITE_DOMAIN"
    fi
    upsert_env_file "$ENV_FILE" CERTBOT_EMAIL "${CERTBOT_EMAIL:-}"
    upsert_env_file "$ENV_FILE" SSL_MODE "$SSL_MODE"
    upsert_env_file "$ENV_FILE" DJANGO_SECRET_KEY "$DJANGO_SECRET_KEY"
    upsert_env_file "$ENV_FILE" PAYMENT_CONFIG_ENCRYPTION_KEY "$PAYMENT_CONFIG_ENCRYPTION_KEY"
    upsert_env_file "$ENV_FILE" PAYMENT_FRONTEND_URL "$PAYMENT_FRONTEND_URL"
    upsert_env_file "$ENV_FILE" DJANGO_DEBUG False
    upsert_env_file "$ENV_FILE" DJANGO_PRODUCTION True
    upsert_env_file "$ENV_FILE" DJANGO_ALLOWED_HOSTS "$DJANGO_ALLOWED_HOSTS"
    upsert_env_file "$ENV_FILE" PUBLIC_PORT "$PUBLIC_PORT"
    upsert_env_file "$ENV_FILE" PUBLIC_HTTP_PORT "$PUBLIC_HTTP_PORT"
    upsert_env_file "$ENV_FILE" PUBLIC_HTTPS_PORT "$PUBLIC_HTTPS_PORT"
    upsert_env_file "$ENV_FILE" LISTEN_HTTP_PORT "$LISTEN_HTTP_PORT"
    upsert_env_file "$ENV_FILE" LISTEN_HTTPS_PORT "$LISTEN_HTTPS_PORT"
    upsert_env_file "$ENV_FILE" BEHIND_NAT "$BEHIND_NAT"
    upsert_env_file "$ENV_FILE" PUBLIC_IP "${PUBLIC_IP:-}"
    upsert_env_file "$ENV_FILE" SSL_HTTP_REDIRECT "${SSL_HTTP_REDIRECT:-auto}"
    upsert_env_file "$ENV_FILE" ADMIN_PUBLIC_PORT "$ADMIN_PUBLIC_PORT"
    upsert_env_file "$ENV_FILE" PORTAL_PUBLIC_PORT "$PORTAL_PUBLIC_PORT"
    upsert_env_file "$ENV_FILE" DJANGO_CSRF_TRUSTED_ORIGINS "$DJANGO_CSRF_TRUSTED_ORIGINS"
    upsert_env_file "$ENV_FILE" DJANGO_CORS_ALLOWED_ORIGINS "$DJANGO_CORS_ALLOWED_ORIGINS"
    upsert_env_file "$ENV_FILE" DJANGO_SECURE_PROXY_SSL_HEADER "$DJANGO_SECURE_PROXY_SSL_HEADER"
    upsert_env_file "$ENV_FILE" DB_PASSWORD "$DB_PASSWORD"
    upsert_env_file "$ENV_FILE" DJANGO_SUPERUSER_USERNAME "$DJANGO_SUPERUSER_USERNAME"
    if [[ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]] && ! is_placeholder "$DJANGO_SUPERUSER_PASSWORD"; then
        upsert_env_file "$ENV_FILE" DJANGO_SUPERUSER_PASSWORD "$DJANGO_SUPERUSER_PASSWORD"
    fi
    upsert_env_file "$ENV_FILE" DJANGO_SUPERUSER_EMAIL "$DJANGO_SUPERUSER_EMAIL"

    ok "部署配置已写入 $ENV_FILE"
}

write_credentials() {
    if [[ "${INSTALL_MODE:-fresh}" == "upgrade" ]]; then
        log "升级模式：不覆盖 $CRED_FILE（避免冲掉首次安装密码）"
        return 0
    fi
    local scheme="http" port
    ssl_material_ready && scheme="https"
    if [[ "$scheme" == "https" ]]; then
        port="${PUBLIC_HTTPS_PORT:-443}"
    else
        port="${PUBLIC_HTTP_PORT:-80}"
    fi
    umask 077
    cat > "$CRED_FILE" <<EOF
# ISPmanager 首次安装凭据（权限 600）。请立即备份后限制查阅。
installed_at=$(date -Iseconds)
site_domain=${SITE_DOMAIN}
portal_url=$(origin_url "$scheme" "$SITE_DOMAIN" "$port")/
admin_url=$(origin_url "$scheme" "$SITE_DOMAIN" "$port")${ADMIN_BASE_PATH}
django_admin=$(origin_url "$scheme" "$SITE_DOMAIN" "$port")/admin/
api_docs=$(origin_url "$scheme" "$SITE_DOMAIN" "$port")/api/docs/
behind_nat=${BEHIND_NAT}
listen_http=${LISTEN_HTTP_PORT}
listen_https=${LISTEN_HTTPS_PORT}
public_http=${PUBLIC_HTTP_PORT}
public_https=${PUBLIC_HTTPS_PORT}
ssl_mode=${SSL_MODE}
django_superuser=${DJANGO_SUPERUSER_USERNAME}
django_superuser_password=${DJANGO_SUPERUSER_PASSWORD}
db_name=${DB_NAME}
db_user=${DB_USER}
db_password=${DB_PASSWORD}
env_file=${ENV_FILE}
runtime_env=${RUNTIME_ENV_FILE}
deploy_layout=${DEPLOY_LAYOUT:-combined}
EOF
    if layout_is_split; then
        cat >> "$CRED_FILE" <<EOF
portal_domain=${PORTAL_DOMAIN}
portal_public_origin=${PORTAL_PUBLIC_ORIGIN}
portal_api_port=${PORTAL_API_PORT}
portal_api_allow=${PORTAL_API_ALLOW_CIDRS}
portal_api_secret=${PORTAL_API_SECRET}
portal_api_upstream=$(origin_url "$scheme" "$SITE_DOMAIN" "$PORTAL_API_PORT")
EOF
    fi
    chmod 600 "$CRED_FILE"
}

step "0/8 安装前检查"
PREFLIGHT_SOURCED=1
# shellcheck disable=SC1091
source "$SCRIPT_DIR/preflight.sh"
unset PREFLIGHT_SOURCED

step "1/8 写入配置模板"
_cli_layout="${DEPLOY_LAYOUT:-}"
if [[ -f "$ENV_FILE" ]]; then
    source_env_file "$ENV_FILE"
fi
[[ -n "$_cli_layout" ]] && DEPLOY_LAYOUT="$_cli_layout"
export DEPLOY_LAYOUT
if [[ ! -f "$ENV_FILE" ]]; then
    [[ -f "$EXAMPLE_FILE" ]] || die "缺少 $EXAMPLE_FILE"
    cp "$EXAMPLE_FILE" "$ENV_FILE"
    ok "已从 .env.example 生成 $ENV_FILE"
fi
if layout_is_split; then
    if frontend_is_prebuilt "$ADMIN_FRONTEND_DIR"; then
        export SKIP_NODE=1
        log "分机发布包已含管理端 dist，系统初始化将跳过 Node.js"
    fi
elif both_frontends_prebuilt; then
    export SKIP_NODE=1
    log "发布包已含前端 dist，系统初始化将跳过 Node.js"
fi

step "2/8 安装系统环境（Python 3.12 / nginx / MySQL / certbot）"
bash "$SCRIPT_DIR/01-init-ubuntu.sh"

step "3/8 生成站点密钥与域名配置"
prepare_env
load_env
if [[ "${INSTALL_MODE:-fresh}" == "upgrade" ]]; then
    backup_existing_install
fi

step "4/8 系统调优"
bash "$SCRIPT_DIR/tune-system.sh"
load_env

step "5/8 初始化数据库"
bash "$SCRIPT_DIR/02-setup-database.sh"

step "6/8 构建依赖与静态资源"
if layout_is_split; then
    if frontend_is_prebuilt "$ADMIN_FRONTEND_DIR"; then
        SKIP_FRONTEND_BUILD=1 bash "$SCRIPT_DIR/build.sh"
    else
        bash "$SCRIPT_DIR/build.sh"
    fi
elif both_frontends_prebuilt; then
    SKIP_FRONTEND_BUILD=1 bash "$SCRIPT_DIR/build.sh"
else
    bash "$SCRIPT_DIR/build.sh"
fi

step "7/8 部署 systemd + nginx"
export INSTALL_MODE
bash "$SCRIPT_DIR/deploy.sh"

step "8/8 启用 HTTPS"
if [[ "$SSL_MODE" == "skip" ]]; then
    log "SSL_MODE=skip，保持 HTTP"
elif [[ "${INSTALL_MODE:-fresh}" == "upgrade" ]] && ssl_material_ready; then
    log "升级模式：证书已存在，只重渲染 nginx，不重新签发"
    SKIP_CERT_ISSUE=1 bash "$SCRIPT_DIR/setup-ssl.sh" || warn "证书渲染未完全成功，站点可能仍可用"
else
    bash "$SCRIPT_DIR/setup-ssl.sh"
fi

step "收尾自检"
write_credentials
set +e
bash "$SCRIPT_DIR/verify-install.sh"
verify_rc=$?
set -e

echo
if [[ "${INSTALL_MODE:-fresh}" == "upgrade" ]]; then
    ok "════════════════════════════════════════════════════"
    ok " 版本升级完成（已有数据与密钥均已保留）"
    ok "════════════════════════════════════════════════════"
    [[ -n "${UPGRADE_BACKUP_DIR:-}" ]] && echo -e "  配置/库备份： $UPGRADE_BACKUP_DIR"
else
    ok "════════════════════════════════════════════════════"
    ok " 一键安装完成"
    ok "════════════════════════════════════════════════════"
fi
if ssl_material_ready; then
    print_site_urls https
else
    print_site_urls http
fi
echo -e "  凭据文件： $CRED_FILE"
echo -e "  服务状态： bash $SCRIPT_DIR/manage-service.sh status"
if [[ "$verify_rc" -ne 0 ]]; then
    warn "自检未完全通过，请根据上方输出排查（服务可能已可用）"
    exit "$verify_rc"
fi
