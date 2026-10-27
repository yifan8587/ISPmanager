#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# preflight.sh — 安装前检查：系统资源 + 是否已有部署
#   可单独执行：sudo bash preflight.sh
#   或被 install.sh source（设置 PREFLIGHT_SOURCED=1）
# 退出码：0 通过（可含告警）  1 硬失败
# ════════════════════════════════════════════════════════════════
if [[ "${PREFLIGHT_SOURCED:-0}" != "1" ]]; then
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
    load_env
fi

PREFLIGHT_FAIL=0
PREFLIGHT_WARN=0
PREFLIGHT_HITS=()

pf_ok()   { ok "$*"; }
pf_warn() { warn "$*"; PREFLIGHT_WARN=$((PREFLIGHT_WARN + 1)); }
pf_fail() { err "$*"; PREFLIGHT_FAIL=$((PREFLIGHT_FAIL + 1)); }
pf_hit()  { PREFLIGHT_HITS+=("$1"); }

step "安装前检查：系统"

if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    log "系统：${PRETTY_NAME:-unknown}"
    if [[ "${ID:-}" != "ubuntu" ]]; then
        pf_warn "官方支持 Ubuntu 22.04 / 24.04，当前为 ${ID:-unknown}"
    elif [[ "${VERSION_ID:-}" != "22.04" && "${VERSION_ID:-}" != "24.04" ]]; then
        pf_warn "官方验证版本为 22.04 / 24.04，当前为 ${VERSION_ID:-unknown}"
    else
        pf_ok "操作系统版本符合要求"
    fi
else
    pf_warn "无法读取 /etc/os-release"
fi

RAM_MB="$(mem_total_mb)"
DISK_MB="$(disk_free_mb /)"
CPU_N="$(cpu_count)"
log "CPU ${CPU_N} 核 · 内存 ${RAM_MB} MB · 根分区剩余 ${DISK_MB} MB"

if [[ "${RAM_MB:-0}" -lt 512 ]]; then
    pf_fail "内存不足 512MB，MySQL + gunicorn 无法稳定运行"
elif [[ "${RAM_MB:-0}" -lt 1024 ]]; then
    pf_warn "内存 ${RAM_MB}MB 偏低，升级时将自动调优 worker / InnoDB / 必要时补 swap"
else
    pf_ok "内存 ${RAM_MB}MB"
fi

if [[ "${DISK_MB:-0}" -lt 800 ]]; then
    pf_fail "根分区剩余不足 800MB，无法安全安装或升级"
elif [[ "${DISK_MB:-0}" -lt 2048 ]]; then
    pf_warn "磁盘剩余 ${DISK_MB}MB，建议清理后再升级（备份库需要空间）"
else
    pf_ok "磁盘剩余 ${DISK_MB}MB"
fi

if ! has_systemd; then
    pf_warn "未检测到 systemd，服务拉起可能不完整（容器 / WSL）"
else
    pf_ok "systemd 可用"
fi

# 程序以 APP_USER 运行、nginx 以 www-data 读静态文件，上级目录必须允许其他用户进入。
if command -v runuser >/dev/null 2>&1 && ! runuser -u nobody -- test -x "$ISPMANAGER_DIR" 2>/dev/null; then
    pf_fail "程序目录 ${ISPMANAGER_DIR} 所在路径其他用户无法进入（常见于解压在 /root 下）。请解压到 /opt/app 后再安装：sudo mkdir -p /opt/app && sudo tar -xzf 安装包 -C /opt/app --strip-components=1 && cd /opt/app"
else
    pf_ok "程序目录 ${ISPMANAGER_DIR}"
fi

step "安装前检查：已有项目"

[[ -f /etc/systemd/system/ispmanager.service ]] && pf_hit "systemd 单元 ispmanager.service"
if has_systemd && systemctl is-active --quiet ispmanager.service 2>/dev/null; then
    pf_hit "ispmanager.service 正在运行"
    pf_ok "后端服务运行中"
fi
[[ -f "${RUNTIME_ENV_FILE:-$RUNTIME_ENV_FILE_DEFAULT}" ]] && pf_hit "运行时环境 /etc/ispmanager.env"
env_has_real_secret "$DEPLOY_DIR/.env" DJANGO_SECRET_KEY && pf_hit "deploy/.env 已有生产密钥"
env_has_real_secret "$DEPLOY_DIR/.env" DB_PASSWORD && pf_hit "deploy/.env 已有数据库密码"
[[ -x "${VENV_DIR:-}/bin/gunicorn" ]] && pf_hit "项目 venv / gunicorn"
[[ -d "${BACKEND_DIR:-}/media" ]] && pf_hit "已有 media 目录"
[[ -f "${ADMIN_FRONTEND_DIR:-}/dist/index.html" ]] && pf_ok "管理端 dist 存在"
if layout_is_split; then
    pf_ok "分机部署：门户不在本机"
    [[ -n "${PORTAL_DOMAIN:-}" && "$PORTAL_DOMAIN" != *example.com ]] && pf_ok "门户域名 ${PORTAL_DOMAIN}"
    _portal_secret="${PORTAL_API_SECRET:-}"
    [[ "${#_portal_secret}" -ge 32 ]] && pf_ok "门户 API 密钥已设置"
    [[ -n "${PORTAL_API_ALLOW_CIDRS:-}" ]] && pf_ok "门户 API 来源网段已设置"
else
    [[ -f "${PORTAL_FRONTEND_DIR:-}/dist/index.html" ]] && pf_ok "门户 dist 存在"
fi

if command -v mysql >/dev/null 2>&1; then
    if db_has_migrations; then
        pf_hit "MySQL 库 ${DB_NAME:-ISPmanager} 已有 django_migrations"
        pf_ok "业务数据库可连接且已初始化"
    elif db_app_connects; then
        pf_ok "业务数据库可连接（尚未见迁移表）"
    else
        log "业务库暂不可连（全新安装正常；升级时请检查 .env 中 DB_*）"
    fi
else
    log "本机尚未安装 mysql 客户端（全新安装将自动安装）"
fi

if [[ -f "${NGINX_AVAIL:-$NGINX_AVAIL_DEFAULT}" ]]; then
    pf_hit "已有 nginx 站点配置"
fi
if ssl_material_ready 2>/dev/null; then
    pf_hit "已有 TLS 证书"
    pf_ok "HTTPS 证书材料就绪，升级时不会重签"
fi

detect_install_mode
if [[ "$INSTALL_MODE" == "upgrade" ]]; then
    ok "判定：已有部署 → 升级模式（保留密钥、数据库、证书与 media）"
    if [[ ${#PREFLIGHT_HITS[@]} -gt 0 ]]; then
        log "探测命中：${PREFLIGHT_HITS[*]}"
    fi
    if [[ "${FORCE_FRESH:-0}" == "1" && "${CONFIRM_FRESH:-0}" != "1" ]]; then
        pf_fail "检测到已有安装。强制重装请同时设置 FORCE_FRESH=1 CONFIRM_FRESH=1（会保留数据库除非你另行清空）"
    fi
else
    ok "判定：全新安装"
    if [[ "${FORCE_UPGRADE:-0}" == "1" ]]; then
        pf_warn "FORCE_UPGRADE=1 但探测分不足，仍按升级处理"
        export INSTALL_MODE="upgrade"
    fi
fi

export INSTALL_MODE
export PREFLIGHT_RAM_MB="$RAM_MB"
export PREFLIGHT_DISK_MB="$DISK_MB"

if [[ "$PREFLIGHT_FAIL" -gt 0 ]]; then
    err "安装前检查未通过（$PREFLIGHT_FAIL 项失败，$PREFLIGHT_WARN 项告警）"
    [[ "${PREFLIGHT_SOURCED:-0}" == "1" ]] && return 1
    exit 1
fi

if [[ "$PREFLIGHT_WARN" -gt 0 ]]; then
    warn "安装前检查通过，但有 $PREFLIGHT_WARN 项告警，脚本将尽量自动调优后继续"
else
    ok "安装前检查通过"
fi

[[ "${PREFLIGHT_SOURCED:-0}" == "1" ]] && return 0
exit 0
