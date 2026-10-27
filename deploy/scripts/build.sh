#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# build.sh — 后端依赖 + 两个前端构建 + collectstatic
#   可无 root 运行（仅构建产物，不写系统目录）
# 用法：bash build.sh
#   发布包仅含 frontend/dist 时自动跳过 npm build
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env

SKIP_FRONTEND_BUILD="${SKIP_FRONTEND_BUILD:-0}"

build_or_verify_frontend() {
    local name="$1"
    local dir="$2"

    if [[ ! -d "$dir" ]]; then
        warn "未找到 $name 目录 $dir，跳过"
        return 0
    fi

    if frontend_is_prebuilt "$dir"; then
        log "$name：检测到预构建发布包（仅有 dist），跳过 npm build"
        require_frontend_dist "$name" "$dir"
        [[ "$name" == "管理端" ]] && require_admin_dist_base
        ok "复用 $name dist：$dir/dist"
        return 0
    fi

    if [[ "$SKIP_FRONTEND_BUILD" == "1" ]]; then
        require_frontend_dist "$name" "$dir"
        [[ "$name" == "管理端" ]] && require_admin_dist_base
        ok "SKIP_FRONTEND_BUILD=1：复用 $name dist"
        return 0
    fi

    [[ -f "$dir/package.json" ]] || die \
        "$name 缺少 package.json，无法 npm build。\n" \
        "  · 若为发布包部署且已含 dist，应存在 $dir/.prebuilt-dist 标记\n" \
        "  · 或手动：SKIP_FRONTEND_BUILD=1 bash build.sh"
    [[ -f "$dir/index.html" ]] || die "$name 缺少 index.html，无法 vite build：$dir"

    command -v node >/dev/null 2>&1 || die "未找到 node，请先执行 01-init-ubuntu.sh（或发布包部署设 SKIP_FRONTEND_BUILD=1）"
    command -v npm  >/dev/null 2>&1 || die "未找到 npm"

    pushd "$dir" >/dev/null
    if [[ "$name" == "管理端" ]]; then
        export VITE_BASE_PATH="${VITE_BASE_PATH:-${ADMIN_BASE_PATH:-/manager}/}"
        log "管理端生产 base：${VITE_BASE_PATH}"
    fi
    if [[ -f package-lock.json ]]; then npm ci || npm install; else npm install; fi
    npm run build
    popd >/dev/null
    require_frontend_dist "$name" "$dir"
    if [[ "$name" == "管理端" ]]; then
        require_admin_dist_base
    fi
    ok "$name 构建产物：$dir/dist"
}

step "0/4 环境检查"
[[ -d "$BACKEND_DIR" ]] || die "后端目录不存在：$BACKEND_DIR"
log "PROJECT_ROOT = $PROJECT_ROOT"
require_python

step "1/4 后端 Python 虚拟环境与依赖"
ensure_venv
"$VENV_DIR/bin/pip" install --upgrade pip wheel >/dev/null
log "安装/升级后端依赖 requirements.txt ..."
"$VENV_DIR/bin/pip" install -r "$BACKEND_DIR/requirements.txt" --upgrade --upgrade-strategy only-if-needed
ok "后端依赖安装完成"

step "2/4 构建管理端前端（ISPmanager/frontend）"
build_or_verify_frontend "管理端" "$ADMIN_FRONTEND_DIR"

step "3/4 构建客户门户前端（ISPweb）"
if layout_is_split; then
    log "分机部署：跳过客户门户构建。门户在另一台服务器用 ISPweb/deploy 安装"
else
    build_or_verify_frontend "客户门户" "$PORTAL_FRONTEND_DIR"
fi

step "4/4 收集 Django 静态资源 collectstatic"
mkdir -p "$DJANGO_STATIC_ROOT" "$DJANGO_MEDIA_ROOT"
pushd "$BACKEND_DIR" >/dev/null
DJANGO_STATIC_ROOT="$DJANGO_STATIC_ROOT" \
DJANGO_MEDIA_ROOT="$DJANGO_MEDIA_ROOT" \
    "$VENV_DIR/bin/python" manage.py collectstatic --noinput --clear
popd >/dev/null
[[ -f "$DJANGO_STATIC_ROOT/admin/css/base.css" ]] || \
    die "collectstatic 未生成 admin 静态：$DJANGO_STATIC_ROOT/admin/css/base.css"
ok "静态资源已收集到：$DJANGO_STATIC_ROOT"

echo
ok "════════════════════════════════════════════════════"
ok " 打包构建完成"
ok "════════════════════════════════════════════════════"
echo -e "  后端依赖：$VENV_DIR"
echo -e "  管理端：  $ADMIN_FRONTEND_DIR/dist"
if layout_is_split; then
    echo -e "  门户端：  另一台服务器（ISPweb）"
else
    echo -e "  门户端：  $PORTAL_FRONTEND_DIR/dist"
fi
echo -e "  静态资源：$DJANGO_STATIC_ROOT"
echo -e "  下一步：  sudo bash deploy.sh"
