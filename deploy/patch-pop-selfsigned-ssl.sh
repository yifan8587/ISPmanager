#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# patch-pop-selfsigned-ssl.sh
# 修复：生产环境同步 POP 时，节点自签证书导致
#   SSLCertVerificationError: certificate verify failed
#
# 适用：已安装的生产机（须先把含本补丁的源码同步到目标机，
#       或解压新的 ispmanager-release-*.tar.gz）
#
# 用法：
#   sudo bash /opt/app/ISPmanager/deploy/patch-pop-selfsigned-ssl.sh
# ════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "$SCRIPT_SELF/scripts/common.sh"
load_env
require_root

step "POP 自签证书同步补丁"

[[ -d "$BACKEND_DIR" ]] || die "找不到后端目录：$BACKEND_DIR"
[[ -x "$VENV_DIR/bin/python" ]] || die "找不到虚拟环境 Python：$VENV_DIR/bin/python"

if ! grep -q 'api_verify_ssl' "$BACKEND_DIR/resource_manager/models.py"; then
    die "源码尚未包含本补丁。请先同步更新后的 ISPmanager，或解压新发布包后再执行。"
fi
if ! grep -q 'resolve_pemanager_ssl_verify' "$BACKEND_DIR/resource_manager/pemanager_client.py"; then
    die "pemanager_client.py 缺少 resolve_pemanager_ssl_verify，源码不完整。"
fi
ok "源码已包含自签证书修复"

step "数据库迁移"
run_manage() {
    pushd "$BACKEND_DIR" >/dev/null
    if [[ -f "${RUNTIME_ENV_FILE:-/etc/ispmanager.env}" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "${RUNTIME_ENV_FILE:-/etc/ispmanager.env}"
        set +a
    fi
    "$VENV_DIR/bin/python" manage.py "$@"
    popd >/dev/null
}
run_manage migrate resource_manager --noinput
ok "resource_manager 迁移完成"

step "收集静态文件"
run_manage collectstatic --noinput >/dev/null || warn "collectstatic 失败，可稍后重试"
ok "collectstatic 已执行"

step "重启服务"
if systemctl list-unit-files ispmanager.service >/dev/null 2>&1; then
    systemctl restart ispmanager.service
    systemctl restart ispmanager-pop-quality.service 2>/dev/null || true
    ok "已重启 ispmanager.service"
else
    warn "未发现 systemd 单元 ispmanager.service，请按现场方式重启 gunicorn"
fi

echo
ok "补丁已应用"
echo "  现有 POP 默认 api_verify_ssl=false，按 IP + 自签证书同步不再因校验证书失败。"
echo "  请回到管理端对该 POP 重新点「从节点同步」。"
echo "  若节点已部署正式域名证书，可在 POP 编辑中打开「校验 HTTPS 证书」。"
echo
echo "  完整重装/新装请使用同目录 ispmanager-release-latest.tar.gz"
