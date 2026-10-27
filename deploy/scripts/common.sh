#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# common.sh — 部署脚本公共库（路径探测 / .env 加载 / 日志 / nginx / SSL）
# 由其它脚本 source 引入，不单独执行。
# ════════════════════════════════════════════════════════════════
set -euo pipefail

# ── 路径探测 ────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(dirname "$SCRIPT_DIR")"          # ISPmanager/deploy
ISPMANAGER_DIR="$(dirname "$DEPLOY_DIR")"      # ISPmanager
DETECTED_ROOT="$(dirname "$ISPMANAGER_DIR")"   # 上级（含 ISPmanager 与 ISPweb）
# load_env 之前也要有路径，避免 set -u 读到未绑定变量
PROJECT_ROOT="${PROJECT_ROOT:-$DETECTED_ROOT}"
BACKEND_DIR="${BACKEND_DIR:-$PROJECT_ROOT/ISPmanager/backend}"
ADMIN_FRONTEND_DIR="${ADMIN_FRONTEND_DIR:-$PROJECT_ROOT/ISPmanager/frontend}"
PORTAL_FRONTEND_DIR="${PORTAL_FRONTEND_DIR:-$PROJECT_ROOT/ISPweb}"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/ISPmanager/venv}"

CERTBOT_WEBROOT="/var/www/certbot"
SELF_SIGNED_DIR="/etc/ispmanager/ssl"
RUNTIME_ENV_FILE_DEFAULT="/etc/ispmanager.env"
NGINX_AVAIL_DEFAULT="/etc/nginx/sites-available/ispmanager.conf"
NGINX_ENABLED_DEFAULT="/etc/nginx/sites-enabled/ispmanager.conf"
NGINX_SNIPPET_DEFAULT="/etc/nginx/snippets/ispmanager-proxy.conf"

# ── 日志 ────────────────────────────────────────────────────────
c_reset='\033[0m'; c_blue='\033[1;34m'; c_green='\033[1;32m'
c_yellow='\033[1;33m'; c_red='\033[1;31m'
log()  { echo -e "${c_blue}[INFO]${c_reset} $*"; }
ok()   { echo -e "${c_green}[ OK ]${c_reset} $*"; }
warn() { echo -e "${c_yellow}[WARN]${c_reset} $*"; }
err()  { echo -e "${c_red}[FAIL]${c_reset} $*" >&2; }
die()  { err "$*"; exit 1; }

step() { echo -e "\n${c_blue}━━━ $* ━━━${c_reset}"; }

# 生成带来源的 Origin URL（80/443 省略端口）
origin_url() {
    local scheme="$1" host="$2" port="$3"
    if { [[ "$scheme" == "http" && "$port" == "80" ]] || [[ "$scheme" == "https" && "$port" == "443" ]]; }; then
        echo "${scheme}://${host}"
    else
        echo "${scheme}://${host}:${port}"
    fi
}

layout_is_split() {
    [[ "${DEPLOY_LAYOUT:-combined}" == "split" ]]
}

# 合并逗号分隔列表并去重
merge_csv_unique() {
    local merged="" item
    for item in $(echo "${1:-}" | tr ',' ' '); do
        item="${item// /}"
        [[ -z "$item" ]] && continue
        [[ ",${merged}," == *",${item},"* ]] && continue
        merged="${merged:+$merged,}$item"
    done
    echo "$merged"
}

is_private_ipv4() {
    local ip="$1"
    [[ "$ip" =~ ^10\.([0-9]{1,3}\.){2}[0-9]{1,3}$ ]] && return 0
    [[ "$ip" =~ ^192\.168\.([0-9]{1,3}\.)[0-9]{1,3}$ ]] && return 0
    [[ "$ip" =~ ^172\.(1[6-9]|2[0-9]|3[0-1])\.([0-9]{1,3}\.)[0-9]{1,3}$ ]] && return 0
    [[ "$ip" =~ ^127\.([0-9]{1,3}\.){2}[0-9]{1,3}$ ]] && return 0
    [[ "$ip" =~ ^169\.254\.([0-9]{1,3}\.)[0-9]{1,3}$ ]] && return 0
    return 1
}

list_lan_ipv4() {
    local ip
    for ip in $(hostname -I 2>/dev/null || true); do
        [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
        is_private_ipv4 "$ip" || continue
        [[ "$ip" == 127.* ]] && continue
        echo "$ip"
    done
}

is_ipv4() {
    [[ "${1:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

is_behind_nat() {
    local flag="${BEHIND_NAT:-auto}"
    case "$flag" in
        1|true|TRUE|yes|YES) return 0 ;;
        0|false|FALSE|no|NO) return 1 ;;
    esac
    [[ -n "$(list_lan_ipv4 | head -n1)" ]]
}

# 走公网 DNS，避开 /etc/hosts 把域名指到内网 IP（NAT 发布常见误判）
dns_a_records_public() {
    local host="$1" rec=""
    [[ -n "$host" ]] || return 1
    if command -v dig >/dev/null 2>&1; then
        local resolver
        for resolver in 8.8.8.8 1.1.1.1; do
            rec="$(dig +short +time=3 +tries=1 A "$host" @"$resolver" 2>/dev/null \
                | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || true)"
            if [[ -n "$rec" ]]; then
                echo "$rec"
                return 0
            fi
        done
    fi
    if command -v host >/dev/null 2>&1; then
        rec="$(host -t A "$host" 8.8.8.8 2>/dev/null | awk '/has address/{print $4}' || true)"
        if [[ -n "$rec" ]]; then
            echo "$rec"
            return 0
        fi
    fi
    return 1
}

publish_extra_server_names() {
    local names="" item
    for item in ${SITE_ALIASES:-}; do
        names="${names:+$names }${item}"
    done
    for item in $(list_lan_ipv4); do
        names="${names:+$names }${item}"
    done
    if [[ -n "${PUBLIC_IP:-}" ]] && is_ipv4 "$PUBLIC_IP"; then
        names="${names:+$names }${PUBLIC_IP}"
    fi
    echo "$names"
}

https_redirect_statement() {
    local port="${PUBLIC_HTTPS_PORT:-443}"
    if [[ "$port" == "443" ]]; then
        echo 'return 301 https://$host$request_uri;'
    else
        echo "return 301 https://\$host:${port}\$request_uri;"
    fi
}

normalize_publish_settings() {
    export LISTEN_HTTP_PORT="${LISTEN_HTTP_PORT:-80}"
    export LISTEN_HTTPS_PORT="${LISTEN_HTTPS_PORT:-443}"
    export PUBLIC_HTTP_PORT="${PUBLIC_HTTP_PORT:-80}"
    if [[ -z "${PUBLIC_HTTPS_PORT:-}" ]]; then
        if [[ -n "${PUBLIC_PORT:-}" && "${PUBLIC_PORT}" != "80" ]]; then
            PUBLIC_HTTPS_PORT="$PUBLIC_PORT"
        else
            PUBLIC_HTTPS_PORT=443
        fi
    fi
    export PUBLIC_HTTPS_PORT
    if is_behind_nat; then
        export BEHIND_NAT=1
    else
        export BEHIND_NAT="${BEHIND_NAT:-0}"
        [[ "$BEHIND_NAT" == "auto" ]] && BEHIND_NAT=0
    fi
    export PUBLIC_IP="${PUBLIC_IP:-}"
    export SSL_HTTP_REDIRECT="${SSL_HTTP_REDIRECT:-auto}"
    export EXTRA_SERVER_NAMES="${EXTRA_SERVER_NAMES:-$(publish_extra_server_names)}"
    if ssl_material_ready 2>/dev/null; then
        export PUBLIC_PORT="${PUBLIC_PORT:-$PUBLIC_HTTPS_PORT}"
    else
        export PUBLIC_PORT="${PUBLIC_PORT:-$PUBLIC_HTTP_PORT}"
    fi
    export ADMIN_PUBLIC_PORT="${PUBLIC_PORT}"
    if layout_is_split; then
        export PORTAL_PUBLIC_PORT="${PORTAL_PUBLIC_PORT:-443}"
    else
        export PORTAL_PUBLIC_PORT="${PUBLIC_PORT}"
    fi
    local ports="${PUBLIC_PORT},${PUBLIC_HTTP_PORT},${PUBLIC_HTTPS_PORT},${LISTEN_HTTP_PORT},${LISTEN_HTTPS_PORT},${CSRF_PUBLIC_PORTS:-}"
    export CSRF_PUBLIC_PORTS="$(merge_csv_unique "$ports")"
}

# 单域名路径分流：
#   门户    https://SITE_DOMAIN/
#   管理端  https://SITE_DOMAIN/manager
#   Django  https://SITE_DOMAIN/admin/
normalize_site_settings() {
    local path="${ADMIN_BASE_PATH:-/manager}"
    path="/${path#/}"
    path="${path%/}"
    [[ -n "$path" ]] || path="/manager"
    export ADMIN_BASE_PATH="$path"
    export VITE_BASE_PATH="${ADMIN_BASE_PATH}/"
    export DEPLOY_LAYOUT="${DEPLOY_LAYOUT:-combined}"
    case "$DEPLOY_LAYOUT" in
        combined|split) ;;
        *) die "DEPLOY_LAYOUT 只能是 combined 或 split" ;;
    esac

    if [[ -z "${SITE_DOMAIN:-}" ]]; then
        if layout_is_split; then
            if [[ -n "${ADMIN_DOMAIN:-}" ]]; then
                SITE_DOMAIN="$ADMIN_DOMAIN"
            fi
        elif [[ -n "${ADMIN_DOMAIN:-}" && -n "${PORTAL_DOMAIN:-}" && "$ADMIN_DOMAIN" == "$PORTAL_DOMAIN" ]]; then
            SITE_DOMAIN="$ADMIN_DOMAIN"
        elif [[ -n "${PORTAL_DOMAIN:-}" && "$PORTAL_DOMAIN" != *example.com ]]; then
            SITE_DOMAIN="$PORTAL_DOMAIN"
        elif [[ -n "${ADMIN_DOMAIN:-}" && "$ADMIN_DOMAIN" != *example.com ]]; then
            SITE_DOMAIN="$ADMIN_DOMAIN"
        elif [[ -n "${PORTAL_DOMAIN:-}" ]]; then
            SITE_DOMAIN="$PORTAL_DOMAIN"
        elif [[ -n "${ADMIN_DOMAIN:-}" ]]; then
            SITE_DOMAIN="$ADMIN_DOMAIN"
        fi
    fi
    SITE_DOMAIN="${SITE_DOMAIN:-www.example.com}"
    export SITE_DOMAIN

    export SITE_ALIASES="${SITE_ALIASES:-}"
    if layout_is_split; then
        export ADMIN_DOMAIN="$SITE_DOMAIN"
        export PORTAL_DOMAIN="${PORTAL_DOMAIN:-}"
        export PORTAL_API_PORT="${PORTAL_API_PORT:-8443}"
        export PORTAL_API_ALLOW_CIDRS="${PORTAL_API_ALLOW_CIDRS:-}"
        export PORTAL_API_SECRET="${PORTAL_API_SECRET:-}"
    else
        if [[ -n "${ADMIN_DOMAIN:-}" && -n "${PORTAL_DOMAIN:-}" && "$ADMIN_DOMAIN" != "$PORTAL_DOMAIN" ]]; then
            warn "同机部署使用单域名路径分流，忽略双域名配置，使用 SITE_DOMAIN=${SITE_DOMAIN}"
        fi
        export ADMIN_DOMAIN="$SITE_DOMAIN"
        export PORTAL_DOMAIN="$SITE_DOMAIN"
    fi
    normalize_publish_settings
}

manager_public_url() {
    local scheme="$1"
    echo "$(origin_url "$scheme" "$SITE_DOMAIN" "${PUBLIC_PORT:-443}")${ADMIN_BASE_PATH}"
}

portal_public_url() {
    local scheme="$1"
    origin_url "$scheme" "$SITE_DOMAIN" "${PUBLIC_PORT:-443}"
}

print_site_urls() {
    local scheme="$1"
    local port base lan
    if [[ "$scheme" == "https" ]]; then
        port="${PUBLIC_HTTPS_PORT:-${PUBLIC_PORT:-443}}"
    else
        port="${PUBLIC_HTTP_PORT:-${PUBLIC_PORT:-80}}"
    fi
    base="$(origin_url "$scheme" "$SITE_DOMAIN" "$port")"
    if layout_is_split; then
        echo -e "  部署方式： 管理端与门户分机"
        echo -e "  管理端：   ${base}${ADMIN_BASE_PATH}"
        echo -e "  Django后台：${base}/admin/"
        echo -e "  管理 API： ${base}/api/"
        if [[ -n "${PORTAL_PUBLIC_ORIGIN:-}" ]]; then
            echo -e "  客户门户： ${PORTAL_PUBLIC_ORIGIN}/"
        elif [[ -n "${PORTAL_DOMAIN:-}" ]]; then
            echo -e "  客户门户： https://${PORTAL_DOMAIN}/"
        fi
        echo -e "  门户 API： ${scheme}://${SITE_DOMAIN}:${PORTAL_API_PORT:-8443}/api/portal/"
    else
        echo -e "  客户门户： ${base}/"
        echo -e "  管理端：   ${base}${ADMIN_BASE_PATH}"
        echo -e "  Django后台：${base}/admin/"
        echo -e "  API：      ${base}/api/"
    fi
    lan="$(list_lan_ipv4 | head -n1 || true)"
    if [[ -n "$lan" ]]; then
        echo -e "  内网HTTP： http://${lan}:${LISTEN_HTTP_PORT:-80}/"
        if ssl_material_ready 2>/dev/null; then
            echo -e "  内网HTTPS：https://${lan}:${LISTEN_HTTPS_PORT:-443}/"
        fi
    fi
    if [[ "${BEHIND_NAT:-0}" == "1" ]]; then
        echo -e "  发布模式： 内网 NAT（路由器转发 ${PUBLIC_HTTP_PORT:-80}→${LISTEN_HTTP_PORT:-80} / ${PUBLIC_HTTPS_PORT:-443}→${LISTEN_HTTPS_PORT:-443}）"
    fi
}

default_payment_frontend_url() {
    local scheme="${1:-https}"
    manager_public_url "$scheme"
}

# 支付回跳必须落到管理端 SPA（/manager/customer/billings），不能只写站点根域名
needs_payment_frontend_reset() {
    local current="${PAYMENT_FRONTEND_URL:-}"
    current="${current%/}"
    [[ -z "$current" ]] && return 0
    [[ "$current" == CHANGE_ME* ]] && return 0
    [[ "$current" == *example.com* ]] && return 0
    [[ "$current" != *"${ADMIN_BASE_PATH}" ]] && return 0
    return 1
}

ensure_payment_frontend_url() {
    local scheme="${1:-https}"
    if [[ "$scheme" == "https" && "${PAYMENT_FRONTEND_URL:-}" == http://* ]]; then
        export PAYMENT_FRONTEND_URL="$(default_payment_frontend_url https)"
        return
    fi
    if needs_payment_frontend_reset; then
        export PAYMENT_FRONTEND_URL="$(default_payment_frontend_url "$scheme")"
    else
        export PAYMENT_FRONTEND_URL="${PAYMENT_FRONTEND_URL%/}"
    fi
}

require_admin_dist_base() {
    local html="$ADMIN_FRONTEND_DIR/dist/index.html"
    local marker="${ADMIN_BASE_PATH}/"
    [[ -f "$html" ]] || die "管理端缺少 dist/index.html"
    grep -F -q "$marker" "$html" || \
        die "管理端 dist 未包含基路径 ${marker}（静态资源会 404）。请用生产模式重新构建前端"
}

# 依据站点域名 / 别名 / 已允许主机 + 端口自动补全 CSRF / CORS
apply_public_origins() {
    local auto="" host item
    local hosts="${SITE_DOMAIN:-}"
    if [[ -n "${SITE_ALIASES:-}" ]]; then
        for item in $SITE_ALIASES; do
            hosts="$(merge_csv_unique "${hosts},${item}")"
        done
    fi
    if [[ -n "${DJANGO_ALLOWED_HOSTS:-}" ]]; then
        hosts="$(merge_csv_unique "${hosts},${DJANGO_ALLOWED_HOSTS}")"
    fi
    if [[ -n "${SITE_PUBLIC_URL:-}" ]]; then
        auto="${SITE_PUBLIC_URL}"
    fi
    local ports="${PUBLIC_PORT:-${ADMIN_PUBLIC_PORT:-}}"
    ports="$(merge_csv_unique "${ports},${ADMIN_PUBLIC_PORT:-},${PORTAL_PUBLIC_PORT:-},${CSRF_PUBLIC_PORTS:-}")"
    for host in $(echo "$hosts" | tr ',' ' '); do
        host="${host// /}"
        [[ -z "$host" || "$host" == "*" ]] && continue
        auto="$(merge_csv_unique "${auto},$(origin_url http "$host" 80)")"
        auto="$(merge_csv_unique "${auto},$(origin_url https "$host" 443)")"
        local port
        for port in $(echo "$ports" | tr ',' ' '); do
            port="${port// /}"
            [[ -z "$port" ]] && continue
            auto="$(merge_csv_unique "${auto},$(origin_url http "$host" "$port")")"
            auto="$(merge_csv_unique "${auto},$(origin_url https "$host" "$port")")"
        done
    done
    if [[ -n "${ADMIN_PUBLIC_URL:-}" ]]; then
        auto="$(merge_csv_unique "${auto},${ADMIN_PUBLIC_URL}")"
    fi
    if [[ -n "${PORTAL_PUBLIC_URL:-}" ]]; then
        auto="$(merge_csv_unique "${auto},${PORTAL_PUBLIC_URL}")"
    fi
    if layout_is_split && [[ -n "${PORTAL_DOMAIN:-}" && "$PORTAL_DOMAIN" != *example.com ]]; then
        export PORTAL_PUBLIC_ORIGIN="$(origin_url https "$PORTAL_DOMAIN" "${PORTAL_PUBLIC_PORT:-443}")"
        auto="$(merge_csv_unique "${auto},${PORTAL_PUBLIC_ORIGIN}")"
    fi

    export DJANGO_CSRF_TRUSTED_ORIGINS="$(merge_csv_unique "${DJANGO_CSRF_TRUSTED_ORIGINS:-},${auto}")"
    export DJANGO_CORS_ALLOWED_ORIGINS="$(merge_csv_unique "${DJANGO_CORS_ALLOWED_ORIGINS:-},${auto}")"
}

# 兼容旧脚本中的私有函数名
_origin_url() { origin_url "$@"; }
_merge_csv_unique() { merge_csv_unique "$@"; }
_apply_public_origins() { apply_public_origins; }

# Django 6 需要 Python ≥ 3.12
python_version_ok() {
    local bin="$1"
    [[ -x "$bin" ]] || return 1
    "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null
}

resolve_python() {
    if [[ -n "${PYTHON_BIN:-}" ]] && python_version_ok "$PYTHON_BIN"; then
        export PYTHON_BIN
        return 0
    fi
    local cand path
    for cand in python3.12 python3.13 python3; do
        path="$(command -v "$cand" 2>/dev/null || true)"
        if [[ -n "$path" ]] && python_version_ok "$path"; then
            export PYTHON_BIN="$path"
            return 0
        fi
    done
    export PYTHON_BIN=""
    return 1
}

require_python() {
    resolve_python || die \
        "需要 Python 3.12+（Django 6）。Ubuntu 22.04 请先执行 01-init-ubuntu.sh 安装 deadsnakes 的 python3.12"
    log "Python：$("$PYTHON_BIN" --version 2>&1)  ($PYTHON_BIN)"
}

ensure_venv() {
    require_python
    if [[ -x "$VENV_DIR/bin/python" ]]; then
        if python_version_ok "$VENV_DIR/bin/python"; then
            return 0
        fi
        warn "已有 venv 的 Python 低于 3.12，将重建：$VENV_DIR"
        rm -rf "$VENV_DIR"
    fi
    log "创建虚拟环境：$VENV_DIR  （$PYTHON_BIN）"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
}

# 按行解析 KEY=VALUE，不 source，避免未加引号的 <email> / 空格把 bash 弄崩
source_env_file() {
    local file="$1" line key val
    [[ -f "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${line//[[:space:]]/}" ]] && continue
        [[ "$line" == *=* ]] || continue
        key="${line%%=*}"
        val="${line#*=}"
        key="${key#"${key%%[![:space:]]*}"}"
        key="${key%"${key##*[![:space:]]}"}"
        [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        if [[ ${#val} -ge 2 ]]; then
            local first="${val:0:1}" last="${val: -1}"
            if [[ "$first" == '"' && "$last" == '"' ]]; then
                val="${val:1:${#val}-2}"
            elif [[ "$first" == "'" && "$last" == "'" ]]; then
                val="${val:1:${#val}-2}"
            fi
        fi
        printf -v "$key" '%s' "$val"
        export "$key"
    done < "$file"
}

# ── 加载 .env ───────────────────────────────────────────────────
load_env() {
    local env_file="$DEPLOY_DIR/.env"
    if [[ -f "$env_file" ]]; then
        source_env_file "$env_file"
        ok "已加载配置：$env_file"
    else
        warn "未找到 $env_file，使用 .env.example 默认值（生产请先 cp .env.example .env）"
        local example="$DEPLOY_DIR/.env.example"
        [[ -f "$example" ]] && source_env_file "$example"
    fi

    # 路径默认值
    PROJECT_ROOT="${PROJECT_ROOT:-$DETECTED_ROOT}"
    [[ -z "$PROJECT_ROOT" ]] && PROJECT_ROOT="$DETECTED_ROOT"
    export PROJECT_ROOT

    export BACKEND_DIR="$PROJECT_ROOT/ISPmanager/backend"
    export ADMIN_FRONTEND_DIR="$PROJECT_ROOT/ISPmanager/frontend"
    export PORTAL_FRONTEND_DIR="$PROJECT_ROOT/ISPweb"
    export VENV_DIR="$PROJECT_ROOT/ISPmanager/venv"

    # 关键变量默认值兜底
    export APP_USER="${APP_USER:-ispmanager}"
    export APP_GROUP="${APP_GROUP:-ispmanager}"
    export APP_BIND="${APP_BIND:-127.0.0.1:8000}"
    export GUNICORN_WORKERS="${GUNICORN_WORKERS:-3}"
    export SITE_DOMAIN="${SITE_DOMAIN:-}"
    export ADMIN_DOMAIN="${ADMIN_DOMAIN:-}"
    export PORTAL_DOMAIN="${PORTAL_DOMAIN:-}"
    export ADMIN_BASE_PATH="${ADMIN_BASE_PATH:-/manager}"
    export DEPLOY_LAYOUT="${DEPLOY_LAYOUT:-combined}"
    export PORTAL_API_PORT="${PORTAL_API_PORT:-8443}"
    export PORTAL_API_ALLOW_CIDRS="${PORTAL_API_ALLOW_CIDRS:-}"
    export PORTAL_API_SECRET="${PORTAL_API_SECRET:-}"
    export SSL_MODE="${SSL_MODE:-auto}"
    export DJANGO_PRODUCTION="${DJANGO_PRODUCTION:-True}"

    # collectstatic 目录默认落在 backend/staticfiles
    export DJANGO_STATIC_ROOT="${DJANGO_STATIC_ROOT:-$BACKEND_DIR/staticfiles}"
    export DJANGO_MEDIA_ROOT="${DJANGO_MEDIA_ROOT:-$BACKEND_DIR/media}"

    export PUBLIC_HTTP_PORT="${PUBLIC_HTTP_PORT:-80}"
    export PUBLIC_HTTPS_PORT="${PUBLIC_HTTPS_PORT:-}"
    export LISTEN_HTTP_PORT="${LISTEN_HTTP_PORT:-80}"
    export LISTEN_HTTPS_PORT="${LISTEN_HTTPS_PORT:-443}"
    export BEHIND_NAT="${BEHIND_NAT:-auto}"
    normalize_site_settings

    export RUNTIME_ENV_FILE="${RUNTIME_ENV_FILE:-$RUNTIME_ENV_FILE_DEFAULT}"
    export NGINX_AVAIL="${NGINX_AVAIL:-$NGINX_AVAIL_DEFAULT}"
    export NGINX_ENABLED="${NGINX_ENABLED:-$NGINX_ENABLED_DEFAULT}"

    resolve_python || true

    # 自动补全 CSRF / CORS 可信来源（含 http/https 与非标准端口）
    apply_public_origins
}

require_root() {
    [[ "$(id -u)" -eq 0 ]] || die "请使用 root 或 sudo 运行：sudo $0"
}

# 是否运行在 systemd 之下（容器/WSL 可能没有）
has_systemd() {
    [[ -d /run/systemd/system ]]
}

# Python 解释器（优先项目 venv；未设置 VENV_DIR 时不要误用 /bin/python）
py() {
    if [[ -n "${VENV_DIR:-}" && -x "$VENV_DIR/bin/python" ]]; then
        "$VENV_DIR/bin/python" "$@"
    elif [[ -n "${PYTHON_BIN:-}" && -x "$PYTHON_BIN" ]]; then
        "$PYTHON_BIN" "$@"
    else
        python3 "$@"
    fi
}

# 是否为「仅 dist、无源码」的预构建前端（发布包典型布局）
frontend_is_prebuilt() {
    local dir="$1"
    [[ -f "$dir/.prebuilt-dist" ]] && return 0
    [[ -f "$dir/dist/index.html" && ! -f "$dir/package.json" ]] && return 0
    return 1
}

both_frontends_prebuilt() {
    local admin="${ADMIN_FRONTEND_DIR:-$DETECTED_ROOT/ISPmanager/frontend}"
    local portal="${PORTAL_FRONTEND_DIR:-$DETECTED_ROOT/ISPweb}"
    frontend_is_prebuilt "$admin" && frontend_is_prebuilt "$portal"
}

# 校验 dist 是否可用
require_frontend_dist() {
    local name="$1"
    local dir="$2"
    [[ -f "$dir/dist/index.html" ]] || die "$name 缺少 dist/index.html：$dir/dist（发布包不完整或尚未构建）"
}

gen_secret() {
    py -c 'import secrets; print(secrets.token_urlsafe(64))'
}

gen_password() {
    py -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(24)))'
}

is_placeholder() {
    local value="${1:-}"
    [[ -z "$value" ]] && return 0
    [[ "$value" == CHANGE_ME* ]] && return 0
    [[ "$value" == *example.com* && "${2:-}" == "email" ]] && return 0
    return 1
}

# 用 Python 安全改写 KEY=VALUE（避免 sed 遇到特殊字符）
upsert_env_file() {
    local file="$1" key="$2" value="$3"
    py - "$file" "$key" "$value" <<'PY'
import pathlib, re, sys
import shlex
path, key, value = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
text = path.read_text(encoding="utf-8") if path.exists() else ""
line = f"{key}={shlex.quote(value)}"
pat = re.compile(rf"^{re.escape(key)}=.*$", re.M)
if pat.search(text):
    text = pat.sub(line, text)
else:
    if text and not text.endswith("\n"):
        text += "\n"
    text += line + "\n"
path.write_text(text, encoding="utf-8")
PY
}

detect_public_ip() {
    local ip=""
    if [[ -n "${PUBLIC_IP:-}" ]] && is_ipv4 "$PUBLIC_IP"; then
        echo "$PUBLIC_IP"
        return 0
    fi
    for url in \
        "https://api.ipify.org" \
        "https://ifconfig.me/ip" \
        "https://icanhazip.com"
    do
        ip="$(curl -4 -fsS --max-time 5 "$url" 2>/dev/null | tr -d '[:space:]' || true)"
        if is_ipv4 "$ip"; then
            echo "$ip"
            return 0
        fi
    done
    return 1
}

dns_a_record() {
    local host="$1" answer=""
    answer="$(dns_a_records_public "$host" 2>/dev/null | awk 'NR==1{print; exit}' || true)"
    if [[ -n "$answer" ]]; then
        echo "$answer"
        return 0
    fi
    if command -v getent >/dev/null 2>&1; then
        answer="$(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1; exit}' || true)"
    fi
    echo "$answer"
}

domain_points_here() {
    local host="$1" public_ip="${2:-${PUBLIC_IP:-}}" dns_ips ip
    [[ -n "$host" ]] || return 1
    dns_ips="$(dns_a_records_public "$host" || true)"
    if [[ -z "$public_ip" ]]; then
        public_ip="$(detect_public_ip || true)"
    fi
    [[ -n "$dns_ips" && -n "$public_ip" ]] || return 1
    for ip in $dns_ips; do
        [[ "$ip" == "$public_ip" ]] && return 0
    done
    return 1
}

# 证书路径：优先 Let's Encrypt，其次自签
ssl_cert_for_domain() {
    local domain="$1"
    if [[ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]]; then
        echo "/etc/letsencrypt/live/${domain}/fullchain.pem"
        return 0
    fi
    if [[ -n "${ADMIN_DOMAIN:-}" && -f "/etc/letsencrypt/live/${ADMIN_DOMAIN}/fullchain.pem" ]]; then
        echo "/etc/letsencrypt/live/${ADMIN_DOMAIN}/fullchain.pem"
        return 0
    fi
    if [[ -f "$SELF_SIGNED_DIR/fullchain.pem" ]]; then
        echo "$SELF_SIGNED_DIR/fullchain.pem"
        return 0
    fi
    return 1
}

ssl_key_for_domain() {
    local domain="$1"
    if [[ -f "/etc/letsencrypt/live/${domain}/privkey.pem" ]]; then
        echo "/etc/letsencrypt/live/${domain}/privkey.pem"
        return 0
    fi
    if [[ -n "${ADMIN_DOMAIN:-}" && -f "/etc/letsencrypt/live/${ADMIN_DOMAIN}/privkey.pem" ]]; then
        echo "/etc/letsencrypt/live/${ADMIN_DOMAIN}/privkey.pem"
        return 0
    fi
    if [[ -f "$SELF_SIGNED_DIR/privkey.pem" ]]; then
        echo "$SELF_SIGNED_DIR/privkey.pem"
        return 0
    fi
    return 1
}

ssl_material_ready() {
    local cert key
    cert="$(ssl_cert_for_domain "$SITE_DOMAIN" || true)"
    key="$(ssl_key_for_domain "$SITE_DOMAIN" || true)"
    [[ -n "$cert" && -f "$cert" && -n "$key" && -f "$key" ]]
}

ssl_is_letsencrypt() {
    [[ -f "/etc/letsencrypt/live/${SITE_DOMAIN}/fullchain.pem" ]] || \
    [[ -f "/etc/letsencrypt/live/${ADMIN_DOMAIN}/fullchain.pem" ]]
}

# 网页在线升级覆盖目录：属主 + 属组可写 + 目录 setgid。
# 使用 u+rwX/g+rwX，避免把 venv 里的可执行文件改成不可执行。
_mark_web_upgrade_writable() {
    local path="$1"
    [[ -e "$path" ]] || return 0
    chown -R "$APP_USER":"$APP_GROUP" "$path" 2>/dev/null || true
    chmod -R u+rwX,g+rwX,o+rX "$path" 2>/dev/null || true
    if [[ -d "$path" ]]; then
        find "$path" -type d -exec chmod u+rwx,g+rwxs,o+rx {} \; 2>/dev/null || true
        find "$path" -type f -name '*.sh' -exec chmod 775 {} \; 2>/dev/null || true
    elif [[ -f "$path" ]]; then
        chmod u+rw,g+rw,o+r "$path" 2>/dev/null || true
    fi
}

# 允许 gunicorn（APP_USER）无密码重启本站服务，否则网页升级后无法热加载
install_web_upgrade_sudoers() {
    local dest="/etc/sudoers.d/ispmanager-web-upgrade"
    local tmp user="$APP_USER"
    [[ "$(id -u)" -eq 0 ]] || return 0
    id "$user" >/dev/null 2>&1 || return 0
    tmp="$(mktemp)"
    cat > "$tmp" <<EOF
# ISPmanager v2 网页在线升级：服务用户可重启本站进程（由 deploy 脚本维护）
Defaults:${user} !requiretty
${user} ALL=(root) NOPASSWD: /bin/systemctl restart ispmanager.service
${user} ALL=(root) NOPASSWD: /bin/systemctl reload ispmanager.service
${user} ALL=(root) NOPASSWD: /bin/systemctl restart ispmanager-pop-quality.service
${user} ALL=(root) NOPASSWD: /bin/systemctl reload nginx
${user} ALL=(root) NOPASSWD: /bin/systemctl reload nginx.service
${user} ALL=(root) NOPASSWD: /usr/bin/systemctl restart ispmanager.service
${user} ALL=(root) NOPASSWD: /usr/bin/systemctl reload ispmanager.service
${user} ALL=(root) NOPASSWD: /usr/bin/systemctl restart ispmanager-pop-quality.service
${user} ALL=(root) NOPASSWD: /usr/bin/systemctl reload nginx
${user} ALL=(root) NOPASSWD: /usr/bin/systemctl reload nginx.service
EOF
    chmod 440 "$tmp"
    if visudo -c -f "$tmp" >/dev/null 2>&1; then
        mv "$tmp" "$dest"
        chmod 440 "$dest"
        ok "已安装网页升级 sudoers：$dest"
    else
        rm -f "$tmp"
        warn "sudoers 校验失败，未写入 $dest"
        return 1
    fi
}

default_web_upgrade_restart_cmd() {
    echo "${SYSTEM_UPGRADE_RESTART_CMD:-sudo -n /bin/systemctl restart ispmanager.service}"
}

# 在线升级由 APP_USER 覆盖文件；root 安装留下的文档/前端/venv 必须改属主
ensure_overlay_ownership() {
    local isp="${ISPMANAGER_DIR:-$PROJECT_ROOT/ISPmanager}"
    if [[ "$(id -u)" -ne 0 ]]; then
        warn "当前不是 root，跳过属主调整（在线升级用户无法 chown）"
        return 0
    fi
    local path
    for path in \
        "$BACKEND_DIR" \
        "$ADMIN_FRONTEND_DIR" \
        "$PORTAL_FRONTEND_DIR" \
        "${VENV_DIR:-}" \
        "$isp/ReadMe" \
        "$isp/VERSION" \
        "$isp/manifest.json" \
        "$isp/apply.sh" \
        "${DJANGO_STATIC_ROOT:-}" \
        "${DJANGO_MEDIA_ROOT:-}"
    do
        [[ -e "$path" ]] || continue
        _mark_web_upgrade_writable "$path"
    done
    if [[ -d "$DEPLOY_DIR" ]]; then
        _mark_web_upgrade_writable "$DEPLOY_DIR"
        if [[ -f "$DEPLOY_DIR/.env" ]]; then
            chown root:"$APP_GROUP" "$DEPLOY_DIR/.env" 2>/dev/null || true
            chmod 640 "$DEPLOY_DIR/.env" 2>/dev/null || true
        fi
    fi
    mkdir -p "${DJANGO_MEDIA_ROOT:-$BACKEND_DIR/media}/system_upgrades" \
        "${DJANGO_MEDIA_ROOT:-$BACKEND_DIR/media}/system_backups" 2>/dev/null || true
    if [[ -n "${DJANGO_MEDIA_ROOT:-}" ]]; then
        _mark_web_upgrade_writable "$DJANGO_MEDIA_ROOT"
    fi
    ensure_nginx_traverse \
        "$ADMIN_FRONTEND_DIR/dist" \
        "$PORTAL_FRONTEND_DIR/dist" \
        "${DJANGO_STATIC_ROOT:-}" \
        "${DJANGO_MEDIA_ROOT:-}" || true
}

# 安装后一次性对齐：属主 + sudoers，保证后续版本可走管理端网页升级
prepare_web_upgrade_permissions() {
    ensure_overlay_ownership
    install_web_upgrade_sudoers || true
    export SYSTEM_UPGRADE_RESTART_CMD="$(default_web_upgrade_restart_cmd)"
}

# nginx 需能遍历到 dist / static / media
ensure_nginx_traverse() {
    local path d
    for path in "$@"; do
        [[ -e "$path" ]] || continue
        d="$path"
        while [[ "$d" != "/" ]]; do
            chmod a+x "$d" 2>/dev/null || true
            d="$(dirname "$d")"
        done
    done
}

write_runtime_env() {
    local dest="${1:-$RUNTIME_ENV_FILE}"
    umask 077
    export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-backend.settings}"
    export PYTHONUNBUFFERED=1
    py - "$dest" <<'PY'
import os, pathlib, shlex, sys
dest = pathlib.Path(sys.argv[1])
keys = [
    "DJANGO_SETTINGS_MODULE", "PYTHONUNBUFFERED", "DJANGO_PRODUCTION",
    "DJANGO_SECRET_KEY", "PAYMENT_FRONTEND_URL", "PAYMENT_CONFIG_ENCRYPTION_KEY",
    "DJANGO_DEBUG", "DJANGO_ALLOWED_HOSTS", "DJANGO_CSRF_TRUSTED_ORIGINS",
    "DJANGO_CORS_ALLOWED_ORIGINS", "DJANGO_SECURE_PROXY_SSL_HEADER",
    "DJANGO_STATIC_ROOT", "DJANGO_MEDIA_ROOT",
    "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT",
    "APP_BIND", "GUNICORN_WORKERS",
    "CHANNEL_LAYER_BACKEND", "CHANNEL_REDIS_URL", "CHANNEL_REDIS_PREFIX",
    "REDIS_URL", "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB",
    "DJANGO_EMAIL_BACKEND", "DJANGO_EMAIL_HOST", "DJANGO_EMAIL_PORT",
    "DJANGO_EMAIL_HOST_USER", "DJANGO_EMAIL_HOST_PASSWORD",
    "DJANGO_EMAIL_USE_TLS", "DJANGO_EMAIL_USE_SSL",
    "DJANGO_DEFAULT_FROM_EMAIL", "DJANGO_EMAIL_TIMEOUT",
    "DJANGO_PEMANAGER_SSL_VERIFY", "DJANGO_WEBHOOK_ALLOW_PRIVATE_URLS",
    "ADMIN_BASE_PATH", "SITE_DOMAIN", "SITE_ALIASES",
    "DEPLOY_LAYOUT", "PORTAL_DOMAIN", "PORTAL_PUBLIC_PORT", "PORTAL_PUBLIC_ORIGIN",
    "PORTAL_API_SECRET", "PORTAL_API_PORT", "PORTAL_API_ALLOW_CIDRS",
    "APP_USER", "APP_GROUP",
    "PUBLIC_PORT", "PUBLIC_HTTP_PORT", "PUBLIC_HTTPS_PORT",
    "LISTEN_HTTP_PORT", "LISTEN_HTTPS_PORT",
    "BEHIND_NAT", "PUBLIC_IP", "SSL_HTTP_REDIRECT", "CSRF_PUBLIC_PORTS",
    "SYSTEM_UPGRADE_RESTART", "SYSTEM_UPGRADE_RESTART_CMD",
]
defaults = {
    "DJANGO_SETTINGS_MODULE": "backend.settings",
    "PYTHONUNBUFFERED": "1",
    "DJANGO_PRODUCTION": "True",
    "DJANGO_DEBUG": "False",
    "DJANGO_SECURE_PROXY_SSL_HEADER": "True",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "3306",
    "DJANGO_EMAIL_HOST": "localhost",
    "DJANGO_EMAIL_PORT": "25",
    "DJANGO_EMAIL_USE_TLS": "False",
    "DJANGO_EMAIL_USE_SSL": "False",
    "DJANGO_EMAIL_TIMEOUT": "15",
    "ADMIN_BASE_PATH": "/manager",
    "APP_USER": "ispmanager",
    "APP_GROUP": "ispmanager",
    "PUBLIC_HTTP_PORT": "80",
    "PUBLIC_HTTPS_PORT": "443",
    "LISTEN_HTTP_PORT": "80",
    "LISTEN_HTTPS_PORT": "443",
    "BEHIND_NAT": "0",
    "SSL_HTTP_REDIRECT": "auto",
    "SYSTEM_UPGRADE_RESTART": "True",
    "SYSTEM_UPGRADE_RESTART_CMD": "sudo -n /bin/systemctl restart ispmanager.service",
}
lines = ["# 由部署脚本生成，请勿手工长期编辑（改 deploy/.env 后重跑 deploy.sh / setup-ssl.sh)"]
for key in keys:
    value = os.environ.get(key, defaults.get(key, ""))
    lines.append(f"{key}={shlex.quote(value)}")
dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    chmod 640 "$dest"
    chown root:"$APP_GROUP" "$dest" 2>/dev/null || true
}

install_nginx_snippet() {
    mkdir -p /etc/nginx/snippets "$CERTBOT_WEBROOT"
    cp "$DEPLOY_DIR/nginx/snippets/proxy_params.conf" "$NGINX_SNIPPET_DEFAULT"
}

# 按 KEY=VALUE 逐行替换模板占位符（避免 sed 碰到引号/分号）
render_template() {
    local template="$1" dest="$2"
    shift 2
    py - "$template" "$dest" "$@" <<'PY'
import pathlib, sys
src, dest = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
text = src.read_text(encoding="utf-8")
for item in sys.argv[3:]:
    key, _, value = item.partition("=")
    text = text.replace(key, value)
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(text, encoding="utf-8")
PY
}

resolve_ssl_http_redirect() {
    local flag="${SSL_HTTP_REDIRECT:-auto}"
    case "$flag" in
        1|true|TRUE|yes|YES) echo 1; return ;;
        0|false|FALSE|no|NO) echo 0; return ;;
    esac
    # NAT 自签且未确认 443 已转发时，不跳转，避免公网 HTTP 站点被打挂
    if [[ "${BEHIND_NAT:-0}" == "1" ]] && ! ssl_is_letsencrypt; then
        echo 0
        return
    fi
    echo 1
}

render_site_locations_snippet() {
    local dest="/etc/nginx/snippets/ispmanager-locations.conf"
    local template="$DEPLOY_DIR/nginx/snippets/site_locations.conf.template"
    if layout_is_split; then
        template="$DEPLOY_DIR/nginx/snippets/site_locations.split.conf.template"
    fi
    mkdir -p /etc/nginx/snippets
    render_template "$template" "$dest" \
        "__ADMIN_BASE_PATH__=$ADMIN_BASE_PATH" \
        "__ADMIN_DIST__=$ADMIN_FRONTEND_DIR/dist" \
        "__PORTAL_DIST__=${PORTAL_FRONTEND_DIR:-/var/empty}/dist" \
        "__STATIC_ROOT__=$DJANGO_STATIC_ROOT" \
        "__MEDIA_ROOT__=$DJANGO_MEDIA_ROOT"
}

portal_api_allow_lines() {
    local raw="${PORTAL_API_ALLOW_CIDRS:-}" item lines=""
    raw="${raw//,/ }"
    [[ -n "${raw// /}" ]] || die "分机部署必须设置 PORTAL_API_ALLOW_CIDRS"
    for item in $raw; do
        [[ "$item" =~ ^[0-9a-fA-F:.]+(/[0-9]{1,3})?$ ]] || die "PORTAL_API_ALLOW_CIDRS 含非法网段：$item"
        lines+="    allow ${item};"$'\n'
    done
    lines+="    deny all;"
    printf '%s' "$lines"
}

require_split_portal_api() {
    layout_is_split || return 0
    [[ -n "${PORTAL_DOMAIN:-}" && "$PORTAL_DOMAIN" != *example.com ]] || \
        die "分机部署必须设置门户域名 PORTAL_DOMAIN"
    [[ "$PORTAL_DOMAIN" != "$SITE_DOMAIN" ]] || \
        die "分机部署时 PORTAL_DOMAIN 必须是另一台门户服务器的域名"
    [[ "${#PORTAL_API_SECRET}" -ge 32 ]] || \
        die "分机部署必须设置至少 32 位 PORTAL_API_SECRET，并在 ISPweb 安装时使用同一密钥"
    [[ -n "${PORTAL_API_ALLOW_CIDRS:-}" ]] || \
        die "分机部署必须设置 PORTAL_API_ALLOW_CIDRS（门户服务器出口地址）"
    [[ "${PORTAL_API_PORT:-8443}" != "${LISTEN_HTTP_PORT:-80}" && "${PORTAL_API_PORT:-8443}" != "${LISTEN_HTTPS_PORT:-443}" ]] || \
        die "PORTAL_API_PORT 不能与站点 80/443 监听端口相同"
    portal_api_allow_lines >/dev/null
}

render_portal_api_nginx() {
    local avail="/etc/nginx/sites-available/ispmanager-portal-api.conf"
    local enabled="/etc/nginx/sites-enabled/ispmanager-portal-api.conf"
    if ! layout_is_split; then
        rm -f "$enabled" "$avail"
        return 0
    fi
    require_split_portal_api
    local listen proto="http" public_port allows
    if ssl_material_ready; then
        proto="https"
        listen="$(cat <<EOF
listen ${PORTAL_API_PORT} ssl;
    listen [::]:${PORTAL_API_PORT} ssl;
    ssl_certificate $(ssl_cert_for_domain "$SITE_DOMAIN");
    ssl_certificate_key $(ssl_key_for_domain "$SITE_DOMAIN");
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
EOF
)"
        public_port="${PUBLIC_HTTPS_PORT:-443}"
    else
        listen="$(cat <<EOF
listen ${PORTAL_API_PORT};
    listen [::]:${PORTAL_API_PORT};
EOF
)"
        public_port="${PUBLIC_HTTP_PORT:-80}"
        warn "门户 API 暂时用明文监听 ${PORTAL_API_PORT}。本步骤不会因此中断，随后的 setup-ssl.sh 会改成 TLS"
    fi
    allows="$(portal_api_allow_lines)"
    mkdir -p /etc/nginx/snippets
    render_template "$DEPLOY_DIR/nginx/snippets/portal_api_proxy.conf.template" \
        /etc/nginx/snippets/ispmanager-portal-proxy.conf \
        "__FORWARDED_PROTO__=$proto" \
        "__PUBLIC_PORT__=$public_port"
    render_template "$DEPLOY_DIR/nginx/ispmanager-portal-api.conf.template" "$avail" \
        "__PORTAL_LISTEN__=$listen" \
        "__SITE_DOMAIN__=$SITE_DOMAIN" \
        "__SITE_ALIASES__=${SITE_ALIASES:-}" \
        "__PORTAL_ALLOWS__=$allows"
    ln -sfn "$avail" "$enabled"
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then
        local item raw="${PORTAL_API_ALLOW_CIDRS//,/ }"
        for item in $raw; do
            ufw allow from "$item" to any port "$PORTAL_API_PORT" proto tcp >/dev/null 2>&1 || true
        done
    fi
}

render_nginx_conf() {
    local dest="${1:-$NGINX_AVAIL}"
    local template admin_cert admin_key hsts extra after
    install_nginx_snippet
    render_site_locations_snippet
    extra="${EXTRA_SERVER_NAMES:-$(publish_extra_server_names)}"

    if ssl_material_ready; then
        template="$DEPLOY_DIR/nginx/ispmanager-ssl.conf.template"
        admin_cert="$(ssl_cert_for_domain "$SITE_DOMAIN")"
        admin_key="$(ssl_key_for_domain "$SITE_DOMAIN")"
        if ssl_is_letsencrypt; then
            hsts='add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;'
        else
            hsts='# HSTS omitted for self-signed certificates'
        fi
        if [[ "$(resolve_ssl_http_redirect)" == "1" ]]; then
            after="$(https_redirect_statement)"
        else
            after="include snippets/ispmanager-locations.conf;"
        fi
        render_template "$template" "$dest" \
            "__SITE_DOMAIN__=$SITE_DOMAIN" \
            "__SITE_ALIASES__=${SITE_ALIASES:-}" \
            "__EXTRA_SERVER_NAMES__=$extra" \
            "__LISTEN_HTTP__=${LISTEN_HTTP_PORT:-80}" \
            "__LISTEN_HTTPS__=${LISTEN_HTTPS_PORT:-443}" \
            "__HTTP_AFTER_ACME__=$after" \
            "__ADMIN_BASE_PATH__=$ADMIN_BASE_PATH" \
            "__ADMIN_DIST__=$ADMIN_FRONTEND_DIR/dist" \
            "__PORTAL_DIST__=$PORTAL_FRONTEND_DIR/dist" \
            "__STATIC_ROOT__=$DJANGO_STATIC_ROOT" \
            "__MEDIA_ROOT__=$DJANGO_MEDIA_ROOT" \
            "__APP_BIND__=$APP_BIND" \
            "__CERTBOT_WEBROOT__=$CERTBOT_WEBROOT" \
            "__SITE_SSL_CERT__=$admin_cert" \
            "__SITE_SSL_KEY__=$admin_key" \
            "__HSTS_HEADER__=$hsts"
    else
        template="$DEPLOY_DIR/nginx/ispmanager.conf.template"
        render_template "$template" "$dest" \
            "__SITE_DOMAIN__=$SITE_DOMAIN" \
            "__SITE_ALIASES__=${SITE_ALIASES:-}" \
            "__EXTRA_SERVER_NAMES__=$extra" \
            "__LISTEN_HTTP__=${LISTEN_HTTP_PORT:-80}" \
            "__ADMIN_BASE_PATH__=$ADMIN_BASE_PATH" \
            "__ADMIN_DIST__=$ADMIN_FRONTEND_DIR/dist" \
            "__PORTAL_DIST__=$PORTAL_FRONTEND_DIR/dist" \
            "__STATIC_ROOT__=$DJANGO_STATIC_ROOT" \
            "__MEDIA_ROOT__=$DJANGO_MEDIA_ROOT" \
            "__APP_BIND__=$APP_BIND" \
            "__CERTBOT_WEBROOT__=$CERTBOT_WEBROOT"
    fi
}

ensure_listen_firewall() {
    command -v ufw >/dev/null 2>&1 || return 0
    ufw allow "${LISTEN_HTTP_PORT:-80}/tcp" >/dev/null 2>&1 || true
    ufw allow "${LISTEN_HTTPS_PORT:-443}/tcp" >/dev/null 2>&1 || true
    # 门户 API 端口只对门户服务器开放；nginx 里还有同样的 allow/deny。
    if layout_is_split && [[ -n "${PORTAL_API_ALLOW_CIDRS:-}" ]]; then
        local cidr
        for cidr in ${PORTAL_API_ALLOW_CIDRS//,/ }; do
            ufw allow from "$cidr" to any port "${PORTAL_API_PORT:-8443}" proto tcp >/dev/null 2>&1 || true
        done
    fi
}

enable_nginx_site() {
    ln -sf "$NGINX_AVAIL" "$NGINX_ENABLED"
    [[ -e /etc/nginx/sites-enabled/default ]] && rm -f /etc/nginx/sites-enabled/default
}

reload_nginx() {
    nginx -t
    if has_systemd; then
        systemctl reload nginx
    else
        nginx -s reload 2>/dev/null || service nginx reload
    fi
}

# ── 安装模式 / 升级探测 ─────────────────────────────────────────

mem_total_mb() {
    awk '/MemTotal:/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0
}

disk_free_mb() {
    local path="${1:-/}"
    df -Pm "$path" 2>/dev/null | awk 'NR==2{print $4}'
}

cpu_count() {
    nproc 2>/dev/null || echo 1
}

recommended_gunicorn_workers() {
    local ram cpu workers
    ram="$(mem_total_mb)"
    cpu="$(cpu_count)"
    if [[ "${ram:-0}" -lt 1500 ]]; then
        workers=2
    elif [[ "${ram:-0}" -lt 3000 ]]; then
        workers=$((cpu + 1))
        [[ "$workers" -gt 3 ]] && workers=3
        [[ "$workers" -lt 2 ]] && workers=2
    else
        workers=$((cpu * 2 + 1))
        [[ "$workers" -gt 8 ]] && workers=8
        [[ "$workers" -lt 2 ]] && workers=2
    fi
    echo "$workers"
}

env_has_real_secret() {
    local file="$1" key="$2" value=""
    [[ -f "$file" ]] || return 1
    value="$(grep -E "^${key}=" "$file" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
    ! is_placeholder "$value"
}

import_runtime_secrets_into_env() {
    local dest="$DEPLOY_DIR/.env"
    local runtime="${RUNTIME_ENV_FILE:-$RUNTIME_ENV_FILE_DEFAULT}"
    [[ -f "$runtime" ]] || return 0
    local key
    for key in DJANGO_SECRET_KEY PAYMENT_CONFIG_ENCRYPTION_KEY DB_PASSWORD \
        DJANGO_ALLOWED_HOSTS SITE_DOMAIN SITE_ALIASES ADMIN_BASE_PATH DB_NAME DB_USER \
        PAYMENT_FRONTEND_URL CHANNEL_REDIS_URL CHANNEL_LAYER_BACKEND \
        DEPLOY_LAYOUT PORTAL_DOMAIN PORTAL_PUBLIC_ORIGIN PORTAL_API_SECRET \
        PORTAL_API_PORT PORTAL_API_ALLOW_CIDRS PUBLIC_IP
    do
        if ! env_has_real_secret "$dest" "$key" && env_has_real_secret "$runtime" "$key"; then
            local value
            value="$(grep -E "^${key}=" "$runtime" | tail -n1 | cut -d= -f2- | tr -d '\r')"
            upsert_env_file "$dest" "$key" "$value"
            log "已从 $runtime 回填 $key"
        fi
    done
}

merge_env_from_example() {
    local dest="$DEPLOY_DIR/.env"
    local example="$DEPLOY_DIR/.env.example"
    [[ -f "$example" ]] || return 0
    [[ -f "$dest" ]] || cp "$example" "$dest"
    py - "$example" "$dest" <<'PY'
import pathlib, re, sys
example, dest = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
src = example.read_text(encoding="utf-8")
text = dest.read_text(encoding="utf-8") if dest.exists() else ""
existing = {
    m.group(1)
    for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)=", text, re.M)
}
added = []
for raw in src.splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key = line.split("=", 1)[0]
    if key in existing:
        continue
    if text and not text.endswith("\n"):
        text += "\n"
    text += raw + "\n"
    existing.add(key)
    added.append(key)
dest.write_text(text, encoding="utf-8")
if added:
    print(",".join(added))
PY
}

db_app_connects() {
    [[ -n "${DB_USER:-}" && -n "${DB_PASSWORD:-}" && -n "${DB_NAME:-}" ]] || return 1
    mysql -u"$DB_USER" -p"$DB_PASSWORD" -h"${DB_HOST:-127.0.0.1}" -P"${DB_PORT:-3306}" \
        --connect-timeout=5 \
        -e "USE \`${DB_NAME}\`; SELECT 1;" >/dev/null 2>&1
}

db_has_migrations() {
    db_app_connects || return 1
    mysql -u"$DB_USER" -p"$DB_PASSWORD" -h"${DB_HOST:-127.0.0.1}" -P"${DB_PORT:-3306}" \
        -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${DB_NAME}' AND table_name='django_migrations';" \
        2>/dev/null | grep -qx '1'
}

detect_install_mode() {
    local score=0
    if [[ "${FORCE_FRESH:-0}" == "1" ]]; then
        export INSTALL_MODE="fresh"
        return 0
    fi
    if [[ "${FORCE_UPGRADE:-0}" == "1" ]]; then
        export INSTALL_MODE="upgrade"
        return 0
    fi
    if [[ -n "${INSTALL_MODE:-}" && ( "$INSTALL_MODE" == "fresh" || "$INSTALL_MODE" == "upgrade" ) ]]; then
        return 0
    fi

    [[ -f /etc/systemd/system/ispmanager.service ]] && score=$((score + 2))
    has_systemd && systemctl is-active --quiet ispmanager.service 2>/dev/null && score=$((score + 2))
    [[ -f "${RUNTIME_ENV_FILE:-$RUNTIME_ENV_FILE_DEFAULT}" ]] && score=$((score + 2))
    env_has_real_secret "$DEPLOY_DIR/.env" DJANGO_SECRET_KEY && score=$((score + 2))
    env_has_real_secret "$DEPLOY_DIR/.env" DB_PASSWORD && score=$((score + 1))
    [[ -x "${VENV_DIR:-}/bin/gunicorn" ]] && score=$((score + 1))
    [[ -d "${BACKEND_DIR:-}/media" ]] && score=$((score + 1))
    if db_has_migrations 2>/dev/null; then
        score=$((score + 3))
    fi

    if [[ "$score" -ge 3 ]]; then
        export INSTALL_MODE="upgrade"
    else
        export INSTALL_MODE="fresh"
    fi
}

backup_existing_install() {
    local stamp dest
    stamp="$(date +%Y%m%d-%H%M%S)"
    dest="${BACKUP_DIR:-/var/backups/ispmanager}/$stamp"
    mkdir -p "$dest"
    [[ -f "$DEPLOY_DIR/.env" ]] && cp -a "$DEPLOY_DIR/.env" "$dest/deploy.env"
    [[ -f "${RUNTIME_ENV_FILE:-$RUNTIME_ENV_FILE_DEFAULT}" ]] && \
        cp -a "${RUNTIME_ENV_FILE:-$RUNTIME_ENV_FILE_DEFAULT}" "$dest/ispmanager.env"
    [[ -f "${NGINX_AVAIL:-$NGINX_AVAIL_DEFAULT}" ]] && \
        cp -a "${NGINX_AVAIL:-$NGINX_AVAIL_DEFAULT}" "$dest/ispmanager.conf"
    [[ -f /etc/systemd/system/ispmanager.service ]] && \
        cp -a /etc/systemd/system/ispmanager.service "$dest/"
    if [[ "${SKIP_DB_BACKUP:-0}" != "1" ]] && db_app_connects; then
        if command -v mysqldump >/dev/null 2>&1; then
            log "备份数据库 $DB_NAME → $dest/database.sql.gz"
            if mysqldump --single-transaction --routines --triggers \
                -u"$DB_USER" -p"$DB_PASSWORD" -h"${DB_HOST:-127.0.0.1}" -P"${DB_PORT:-3306}" \
                "$DB_NAME" 2>/dev/null | gzip > "$dest/database.sql.gz"; then
                ok "数据库备份完成"
            else
                warn "数据库备份失败，继续升级（可用 SKIP_DB_BACKUP=1 跳过）"
                rm -f "$dest/database.sql.gz"
            fi
        fi
    else
        log "跳过数据库备份（SKIP_DB_BACKUP=${SKIP_DB_BACKUP:-0}）"
    fi
    echo "$dest" > "$dest/README.txt"
    {
        echo "ISPmanager 升级备份 $stamp"
        echo "restore env: cp $dest/deploy.env $DEPLOY_DIR/.env"
        echo "restore runtime: cp $dest/ispmanager.env ${RUNTIME_ENV_FILE:-$RUNTIME_ENV_FILE_DEFAULT}"
    } > "$dest/README.txt"
    chmod -R go-rwx "$dest" 2>/dev/null || true
    export UPGRADE_BACKUP_DIR="$dest"
    ok "升级备份：$dest"
}

run_post_upgrade_hooks() {
    local py="${VENV_DIR:-}/bin/python"
    local envf="${RUNTIME_ENV_FILE:-$RUNTIME_ENV_FILE_DEFAULT}"
    if [[ ! -x "$py" || ! -d "${BACKEND_DIR:-}" ]]; then
        warn "venv 或后端目录不可用，跳过权限/日志同步"
        return 0
    fi
    (
        set +e
        cd "$BACKEND_DIR" || exit 0
        if [[ -f "$envf" ]]; then
            set -a
            # shellcheck disable=SC1090
            source_env_file "$envf"
            set +a
        fi
        if "$py" manage.py sync_permissions --skip-checks >/dev/null 2>&1 \
            || "$py" manage.py sync_permissions >/dev/null 2>&1; then
            echo "[ OK ] 权限目录已同步"
        else
            echo "[WARN] sync_permissions 跳过或失败（命令不存在也可忽略）"
        fi
        if "$py" manage.py sync_audit_logs --skip-checks >/dev/null 2>&1 \
            || "$py" manage.py sync_audit_logs >/dev/null 2>&1; then
            echo "[ OK ] 操作日志已回填/同步"
        else
            echo "[WARN] sync_audit_logs 跳过或失败（未安装 log_manager 时可忽略）"
        fi
        exit 0
    ) || true
    return 0
}
