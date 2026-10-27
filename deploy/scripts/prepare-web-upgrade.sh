#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# prepare-web-upgrade.sh — 对齐网页在线升级权限
#   1) 程序目录属主改为 APP_USER（默认 ispmanager）
#   2) 目录 setgid + 组可写，后续覆盖文件仍可被服务进程写入
#   3) 安装 sudoers，允许服务用户重启 ispmanager / 重载 nginx
#
# 用法：
#   sudo bash prepare-web-upgrade.sh
#   sudo bash prepare-web-upgrade.sh --root /opt/app
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
    export VENV_DIR="$DEST_ROOT/ISPmanager/venv"
    export DEPLOY_DIR="$DEST_ROOT/ISPmanager/deploy"
    export ISPMANAGER_DIR="$DEST_ROOT/ISPmanager"
    export DJANGO_STATIC_ROOT="${DJANGO_STATIC_ROOT:-$BACKEND_DIR/staticfiles}"
    export DJANGO_MEDIA_ROOT="${DJANGO_MEDIA_ROOT:-$BACKEND_DIR/media}"
fi

step "准备网页在线升级权限（用户 ${APP_USER}:${APP_GROUP}）"
prepare_web_upgrade_permissions
ok "后续版本可在管理端「系统维护」上传 ispmanager-update-*.tar.gz 自动覆盖并重启"
