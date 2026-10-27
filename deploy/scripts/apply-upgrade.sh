#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# apply-upgrade.sh — 已装系统完整程序/数据库升级
#   pip 升级依赖 → migrate --skip-checks → collectstatic → 权限/日志同步
# 用法：
#   sudo bash apply-upgrade.sh
#   在线升级 apply.sh 也会调用本脚本
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env

pybin="${VENV_DIR}/bin/python"
pipbin="${VENV_DIR}/bin/pip"
[[ -x "$pybin" ]] || die "未找到 venv：$VENV_DIR，请先执行 build.sh / install.sh"
[[ -f "$BACKEND_DIR/requirements.txt" ]] || die "缺少 $BACKEND_DIR/requirements.txt"

run_manage() {
    (
        cd "$BACKEND_DIR" || exit 1
        if [[ -f "${RUNTIME_ENV_FILE:-$RUNTIME_ENV_FILE_DEFAULT}" ]]; then
            set -a
            # shellcheck disable=SC1090
            source_env_file "${RUNTIME_ENV_FILE:-$RUNTIME_ENV_FILE_DEFAULT}"
            set +a
        fi
        "$pybin" manage.py "$@"
    )
}

step "升级 Python 依赖"
"$pipbin" install --upgrade pip wheel >/dev/null
"$pipbin" install -r "$BACKEND_DIR/requirements.txt" --upgrade --upgrade-strategy only-if-needed
ok "后端依赖已按 requirements.txt 对齐"

step "数据库迁移"
if ! run_manage migrate --noinput --skip-checks; then
    warn "migrate --skip-checks 失败，再试标准 migrate"
    run_manage migrate --noinput || die "数据库迁移失败，已中止升级（避免程序新、库旧）"
fi
ok "数据库迁移完成"

step "收集静态资源"
mkdir -p "$DJANGO_STATIC_ROOT" "$DJANGO_MEDIA_ROOT"
if ! run_manage collectstatic --noinput --skip-checks; then
    run_manage collectstatic --noinput || die "collectstatic 失败"
fi
[[ -f "$DJANGO_STATIC_ROOT/admin/css/base.css" ]] || \
    die "collectstatic 未生成 admin 静态：$DJANGO_STATIC_ROOT/admin/css/base.css"
chmod -R a+rX "$DJANGO_STATIC_ROOT" 2>/dev/null || true
ok "静态资源已更新（含 Django Admin）"

step "同步权限与操作日志"
run_post_upgrade_hooks || warn "后置同步未完全成功"

step "对齐在线升级可写属主"
prepare_web_upgrade_permissions
ok "程序与数据库升级步骤已全部执行"
