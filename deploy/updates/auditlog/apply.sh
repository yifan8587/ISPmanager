#!/usr/bin/env bash
# 将本更新包应用到已安装的 ISPmanager 目录
set -euo pipefail

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES="$PKG_ROOT/files"

c_blue='\033[1;34m'; c_green='\033[1;32m'; c_red='\033[1;31m'; c_reset='\033[0m'
log() { echo -e "${c_blue}[INFO]${c_reset} $*"; }
ok()  { echo -e "${c_green}[ OK ]${c_reset} $*"; }
die() { echo -e "${c_red}[FAIL]${c_reset} $*" >&2; exit 1; }

TARGET="${1:-}"
if [[ -z "$TARGET" && -d /opt/app/ISPmanager ]]; then
    TARGET="/opt/app/ISPmanager"
fi
[[ -n "$TARGET" ]] || die "用法：sudo bash apply.sh /opt/app/ISPmanager"
TARGET="$(cd "$TARGET" && pwd)"
[[ -d "$TARGET/backend" ]] || die "目标不是 ISPmanager 根目录：$TARGET"
[[ -d "$FILES" ]] || die "更新包不完整，缺少 files/：$FILES"

PY=""
for candidate in "$TARGET/venv/bin/python" "$TARGET/backend/venv/bin/python"; do
    if [[ -x "$candidate" ]]; then
        PY="$candidate"
        break
    fi
done
[[ -n "$PY" ]] || die "未找到项目 venv（$TARGET/venv）。请先完成首次安装。"

step() { echo -e "\n${c_blue}━━━ $* ━━━${c_reset}"; }

step "1/4 覆盖后端与管理端文件"
tar -C "$FILES" -cf - . | tar -C "$TARGET" -xf -
chmod -R a+rX "$TARGET/frontend/dist" 2>/dev/null || true
ok "已同步到 $TARGET"

step "2/4 数据库迁移"
(
    cd "$TARGET/backend"
    if [[ -f /etc/ispmanager.env ]]; then
        set -a
        # shellcheck disable=SC1091
        source /etc/ispmanager.env
        set +a
    fi
    "$PY" manage.py migrate log_manager --noinput
)
ok "log_manager 迁移完成"

step "3/4 回填历史日志并同步权限"
(
    cd "$TARGET/backend"
    if [[ -f /etc/ispmanager.env ]]; then
        set -a
        # shellcheck disable=SC1091
        source /etc/ispmanager.env
        set +a
    fi
    "$PY" manage.py sync_audit_logs
)
ok "sync_audit_logs 完成"

step "4/4 重启后端"
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files ispmanager.service >/dev/null 2>&1; then
    if [[ "$(id -u)" -eq 0 ]]; then
        systemctl restart ispmanager.service
        systemctl is-active --quiet ispmanager-pop-quality.service && systemctl restart ispmanager-pop-quality.service || true
        ok "已重启 ispmanager.service"
    else
        echo "请以 root 执行：sudo systemctl restart ispmanager.service"
    fi
else
    log "未检测到 systemd 服务，请自行重启 runserver / gunicorn"
fi

echo
ok "操作日志更新已应用"
echo "  管理端：系统设置 → 操作日志"
echo "  若菜单未出现，请重新登录以刷新权限"
