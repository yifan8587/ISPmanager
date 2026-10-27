#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# update-from-package.sh — 已装系统全量更新（不影响业务数据）
#
# 覆盖：后端代码、管理端/门户 dist、部署脚本与文档
# 保留：MySQL 业务数据、deploy/.env、/etc/ispmanager.env、media 上传、
#       证书、venv（只升级依赖）、超级管理员与客户账号
#
# 用法：
#   sudo bash update-from-package.sh
#   sudo bash update-from-package.sh --root /opt/app
#   sudo bash update-from-package.sh --payload /tmp/isp-update --root /opt/app
#   sudo SKIP_DB_BACKUP=1 bash update-from-package.sh
# ════════════════════════════════════════════════════════════════
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_ROOT=""
PAYLOAD=""
SKIP_BACKUP="${SKIP_DB_BACKUP:-0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --root|-r)
            DEST_ROOT="${2:-}"
            shift 2
            ;;
        --payload|-p)
            PAYLOAD="${2:-}"
            shift 2
            ;;
        --skip-db-backup)
            SKIP_BACKUP=1
            shift
            ;;
        --help|-h)
            sed -n '2,16p' "$0"
            exit 0
            ;;
        *)
            echo "未知参数：$1" >&2
            exit 1
            ;;
    esac
done

[[ "$(id -u)" -eq 0 ]] || { echo "请使用 root：sudo $0" >&2; exit 1; }

is_live_install() {
    local root="$1"
    [[ -n "$root" && -d "$root/ISPmanager/backend" ]] || return 1
    # 更新包解压目录没有运行时痕迹；已装系统至少有其一
    [[ -f "$root/ISPmanager/deploy/.env" ]] && return 0
    [[ -x "$root/ISPmanager/venv/bin/python" ]] && return 0
    [[ -d "$root/ISPmanager/venv" ]] && return 0
    return 1
}

guess_payload() {
    local here
    if [[ -n "${PACKAGE_PAYLOAD:-}" && -d "${PACKAGE_PAYLOAD}/ISPmanager/backend" ]]; then
        echo "$PACKAGE_PAYLOAD"
        return 0
    fi
    here="$(cd "$SELF_DIR/../../.." && pwd)"
    if [[ -d "$here/ISPmanager/backend" ]]; then
        echo "$here"
        return 0
    fi
    return 1
}

guess_dest() {
    local cand
    if [[ -n "${UPDATE_ROOT:-}" ]] && is_live_install "${UPDATE_ROOT}"; then
        echo "$UPDATE_ROOT"
        return 0
    fi
    for cand in /opt/app /opt/ispmanager /srv/ispmanager; do
        if is_live_install "$cand"; then
            echo "$cand"
            return 0
        fi
    done
    return 1
}

sync_tree() {
    local src="$1" dest="$2"
    shift 2
    mkdir -p "$dest"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete "$@" "$src" "$dest"
    else
        echo "[WARN] 未安装 rsync，改用 tar 覆盖（不会删除目标中的多余文件）" >&2
        tar -C "$(dirname "$src")" -cf - "$(basename "$src")" | tar -C "$(dirname "$dest")" -xf -
    fi
}

PAYLOAD="${PAYLOAD:-$(guess_payload || true)}"
[[ -n "$PAYLOAD" && -d "$PAYLOAD/ISPmanager/backend" ]] || {
    echo "找不到更新包内容（需要 ISPmanager/backend）。请用 --payload 指定解压目录。" >&2
    exit 1
}
PAYLOAD="$(cd "$PAYLOAD" && pwd)"

if [[ -z "$DEST_ROOT" ]]; then
    DEST_ROOT="$(guess_dest || true)"
fi
[[ -n "$DEST_ROOT" ]] || DEST_ROOT="/opt/app"
[[ -d "$DEST_ROOT" ]] || {
    echo "目标目录不存在：$DEST_ROOT" >&2
    echo "全量更新包不能用于全新安装。请使用 ispmanager-install-*.tar.gz 与 install.sh。" >&2
    exit 1
}
DEST_ROOT="$(cd "$DEST_ROOT" && pwd)"

if ! is_live_install "$DEST_ROOT"; then
    echo "未检测到已安装系统：$DEST_ROOT（缺少 deploy/.env 或 venv）" >&2
    echo "全量更新包不能用于全新安装。请使用 ispmanager-install-*.tar.gz 与 install.sh。" >&2
    exit 1
fi

if [[ -f "$PAYLOAD/ISPmanager/deploy/.env" ]]; then
    echo "拒绝：更新包内含 deploy/.env，存在覆盖生产密钥风险。" >&2
    exit 1
fi

DEST_COMMON="$DEST_ROOT/ISPmanager/deploy/scripts/common.sh"
[[ -f "$DEST_COMMON" ]] || {
    echo "目标机缺少 $DEST_COMMON，无法安全更新。" >&2
    exit 1
}

# 用目标机脚本解析路径，避免探测到解压目录
# shellcheck disable=SC1090
source "$DEST_COMMON"
require_root
export PROJECT_ROOT="$DEST_ROOT"
load_env
export PROJECT_ROOT="$DEST_ROOT"
export BACKEND_DIR="$DEST_ROOT/ISPmanager/backend"
export ADMIN_FRONTEND_DIR="$DEST_ROOT/ISPmanager/frontend"
export PORTAL_FRONTEND_DIR="$DEST_ROOT/ISPweb"
export VENV_DIR="$DEST_ROOT/ISPmanager/venv"
export DEPLOY_DIR="$DEST_ROOT/ISPmanager/deploy"

step "全量更新（保留业务数据）"
log "更新包：$PAYLOAD"
log "目标机：$DEST_ROOT"
log "将保留：数据库 / deploy/.env / media / 证书 / 运行时密钥"

export SKIP_DB_BACKUP="$SKIP_BACKUP"
if declare -F backup_existing_install >/dev/null; then
    backup_existing_install
else
    warn "目标机脚本过旧，仅备份 .env"
    mkdir -p /var/backups/ispmanager
    cp -a "$DEST_ROOT/ISPmanager/deploy/.env" "/var/backups/ispmanager/deploy.env.$(date +%Y%m%d-%H%M%S)" || true
fi

if [[ "$PAYLOAD" == "$DEST_ROOT" ]]; then
    log "更新包已解压在目标目录，跳过文件同步，直接执行迁移与重启"
else
    command -v rsync >/dev/null 2>&1 || die "需要 rsync：apt-get install -y rsync"
    step "同步程序文件（不覆盖 .env / media / venv）"

    sync_tree "$PAYLOAD/ISPmanager/backend/" "$DEST_ROOT/ISPmanager/backend/" \
        --exclude '/media/' \
        --exclude '/staticfiles/' \
        --exclude '*.sqlite3' \
        --exclude '__pycache__/' \
        --exclude '*.pyc'

    sync_tree "$PAYLOAD/ISPmanager/deploy/" "$DEST_ROOT/ISPmanager/deploy/" \
        --exclude '/.env' \
        --exclude '/*.tar.gz' \
        --exclude '/*.tar.gz.sha256' \
        --exclude '/build/'

    mkdir -p "$DEST_ROOT/ISPmanager/frontend/dist"
    sync_tree "$PAYLOAD/ISPmanager/frontend/dist/" "$DEST_ROOT/ISPmanager/frontend/dist/"
    if [[ -f "$PAYLOAD/ISPmanager/frontend/.prebuilt-dist" ]]; then
        cp -f "$PAYLOAD/ISPmanager/frontend/.prebuilt-dist" "$DEST_ROOT/ISPmanager/frontend/.prebuilt-dist"
    fi
    if [[ -d "$PAYLOAD/ISPweb/dist" ]]; then
        mkdir -p "$DEST_ROOT/ISPweb/dist"
        sync_tree "$PAYLOAD/ISPweb/dist/" "$DEST_ROOT/ISPweb/dist/"
        if [[ -f "$PAYLOAD/ISPweb/.prebuilt-dist" ]]; then
            cp -f "$PAYLOAD/ISPweb/.prebuilt-dist" "$DEST_ROOT/ISPweb/.prebuilt-dist"
        fi
    elif layout_is_split; then
        log "分机部署更新包不含门户页面"
    else
        die "更新包缺少 ISPweb/dist。同机部署需要门户静态文件，或把 DEPLOY_LAYOUT 设为 split"
    fi

    if [[ -d "$PAYLOAD/ISPmanager/ReadMe" ]]; then
        mkdir -p "$DEST_ROOT/ISPmanager/ReadMe"
        sync_tree "$PAYLOAD/ISPmanager/ReadMe/" "$DEST_ROOT/ISPmanager/ReadMe/"
    fi
    for f in VERSION manifest.json apply.sh; do
        [[ -f "$PAYLOAD/ISPmanager/$f" ]] && cp -f "$PAYLOAD/ISPmanager/$f" "$DEST_ROOT/ISPmanager/$f"
    done
    [[ -f "$DEST_ROOT/ISPmanager/apply.sh" ]] && chmod +x "$DEST_ROOT/ISPmanager/apply.sh"
    find "$DEST_ROOT/ISPmanager/deploy/scripts" -type f -name '*.sh' -exec chmod +x {} \;

    ok "程序文件已同步"
fi

# 同步后改用新脚本（含 apply-upgrade.sh）
# shellcheck disable=SC1090
source "$DEST_ROOT/ISPmanager/deploy/scripts/common.sh"
export PROJECT_ROOT="$DEST_ROOT"
load_env
export PROJECT_ROOT="$DEST_ROOT"
export BACKEND_DIR="$DEST_ROOT/ISPmanager/backend"
export ADMIN_FRONTEND_DIR="$DEST_ROOT/ISPmanager/frontend"
export PORTAL_FRONTEND_DIR="$DEST_ROOT/ISPweb"
export VENV_DIR="$DEST_ROOT/ISPmanager/venv"
export DEPLOY_DIR="$DEST_ROOT/ISPmanager/deploy"

step "执行依赖 / 迁移 / 静态收集"
bash "$DEST_ROOT/ISPmanager/deploy/scripts/apply-upgrade.sh"
if [[ -f "$DEST_ROOT/ISPmanager/deploy/scripts/prepare-web-upgrade.sh" ]]; then
    bash "$DEST_ROOT/ISPmanager/deploy/scripts/prepare-web-upgrade.sh" --root "$DEST_ROOT" || true
fi

step "重载服务"
if [[ -f /etc/systemd/system/ispmanager.service ]]; then
    write_runtime_env "${RUNTIME_ENV_FILE:-$RUNTIME_ENV_FILE_DEFAULT}" || true
    render_nginx_conf "${NGINX_AVAIL:-$NGINX_AVAIL_DEFAULT}" || warn "nginx 模板渲染失败"
    render_portal_api_nginx || warn "门户 API 站点渲染失败"
    enable_nginx_site || true
    systemctl restart ispmanager.service || warn "重启 ispmanager 失败"
    systemctl restart ispmanager-pop-quality.service 2>/dev/null || true
    systemctl enable ispmanager-instance-expiry.timer ispmanager-maintenance.timer ispmanager-daily-jobs.timer >/dev/null 2>&1 || true
    systemctl start ispmanager-instance-expiry.timer ispmanager-maintenance.timer ispmanager-daily-jobs.timer >/dev/null 2>&1 || true
    reload_nginx || warn "nginx reload 失败"
fi

echo
ok "════════════════════════════════════════════════════"
ok " 全量更新完成（业务数据未改写）"
ok "════════════════════════════════════════════════════"
echo -e "  目标目录： $DEST_ROOT"
[[ -n "${UPGRADE_BACKUP_DIR:-}" ]] && echo -e "  配置/库备份： $UPGRADE_BACKUP_DIR"
echo -e "  说明：仅做了 schema 迁移，客户、订单、账单、上传文件均保留"
