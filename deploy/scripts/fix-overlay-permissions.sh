#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# fix-overlay-permissions.sh — 修复在线升级 Permission denied
#
# 安装脚本以 root 落盘后，ReadMe / 前端 / 门户仍属 root，
# gunicorn（APP_USER）覆盖时会出现：
#   [Errno 13] Permission denied: '.../ReadMe/xxx.md'
#
# 用法：
#   sudo bash fix-overlay-permissions.sh
#   sudo bash fix-overlay-permissions.sh --root /opt/app
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
require_root

DEST_ROOT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --root|-r)
            DEST_ROOT="${2:-}"
            shift 2
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

if [[ -n "$DEST_ROOT" ]]; then
    export PROJECT_ROOT="$DEST_ROOT"
fi
load_env
if [[ -n "$DEST_ROOT" ]]; then
    export PROJECT_ROOT="$DEST_ROOT"
    export BACKEND_DIR="$DEST_ROOT/ISPmanager/backend"
    export ADMIN_FRONTEND_DIR="$DEST_ROOT/ISPmanager/frontend"
    export PORTAL_FRONTEND_DIR="$DEST_ROOT/ISPweb"
    export DEPLOY_DIR="$DEST_ROOT/ISPmanager/deploy"
    export ISPMANAGER_DIR="$DEST_ROOT/ISPmanager"
fi

step "调整在线升级覆盖目录属主为 ${APP_USER}:${APP_GROUP}"
prepare_web_upgrade_permissions
ok "已完成。网页升级目录可写，服务用户可无密码重启 ispmanager。"
echo "请回到管理端「系统维护」重新执行升级，或："
echo "  sudo bash /tmp/isp-update/update.sh --root ${PROJECT_ROOT}"
