#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# package.sh — 一次构建，打出两套发布包
#   1) 安装部署包 ispmanager-install-<ver>-<ts>.tar.gz
#   2) 全量更新包 ispmanager-update-<ver>-<ts>.tar.gz（不影响业务数据）
#
# 用法：
#   bash package.sh
#   SKIP_BUILD=1 bash package.sh
#   OUTPUT_DIR=/tmp bash package.sh
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env

SKIP_BUILD="${SKIP_BUILD:-0}"
OUTPUT_DIR="${OUTPUT_DIR:-$DEPLOY_DIR}"
TS="$(date +%Y%m%d-%H%M%S)"
STAGE="$(mktemp -d)"
PAYLOAD_ROOT="$STAGE/payload"

cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

read_app_version() {
    local v
    v="$(tr -d ' \t\r' < "$PROJECT_ROOT/ISPmanager/VERSION" 2>/dev/null | head -n1)"
    v="${v#v}"
    v="${v#V}"
    echo "${v:-1.1.2}"
}

normalize_release_modes() {
    local root="$1"
    find "$root" -type d -exec chmod 755 {} \;
    find "$root" -type f -exec chmod 644 {} \;
    find "$root" -type f -name '*.sh' -exec chmod 755 {} \;
    [[ -f "$root/ISPmanager/apply.sh" ]] && chmod 755 "$root/ISPmanager/apply.sh"
}

write_apply_sh() {
    local dest="$1"
    cat > "$dest" <<'APPLY'
#!/usr/bin/env bash
# 在线升级入口：覆盖已由维护模块完成，这里只跑依赖/库/静态同步
set -euo pipefail
DEST="${1:-${ISPMANAGER_ROOT:-}}"
if [[ -z "$DEST" || ! -d "$DEST" ]]; then
    echo "缺少 ISPmanager 根目录" >&2
    exit 1
fi
SCRIPT="$DEST/deploy/scripts/apply-upgrade.sh"
if [[ ! -f "$SCRIPT" ]]; then
    echo "缺少 $SCRIPT，请确认更新包已覆盖 deploy/scripts" >&2
    exit 1
fi
exec bash "$SCRIPT"
APPLY
    chmod +x "$dest"
}

stage_payload() {
    local root="$1" kind="$2" release_name="$3"
    mkdir -p "$root/ISPmanager/frontend"
    if ! layout_is_split; then
        mkdir -p "$root/ISPweb"
    fi

    log "收集后端源码与 deploy（不含 .env / media / venv）..."
    tar -cf - -C "$PROJECT_ROOT/ISPmanager" \
        --exclude='venv' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='*.pyo' \
        --exclude='.git' \
        --exclude='backend/staticfiles' \
        --exclude='backend/media' \
        --exclude='backend/*.sqlite3' \
        --exclude='backend/db.sqlite3' \
        --exclude='deploy/.env' \
        --exclude='deploy/*.tar.gz' \
        --exclude='deploy/*.tar.gz.sha256' \
        --exclude='deploy/build' \
        backend deploy ReadMe VERSION \
        | tar -xf - -C "$root/ISPmanager"

    mkdir -p "$root/ISPmanager/frontend/dist"
    cp -r "$ADMIN_FRONTEND_DIR/dist/." "$root/ISPmanager/frontend/dist/"
    cat > "$root/ISPmanager/frontend/.prebuilt-dist" <<EOF
# 预构建管理端，目标机无需 Node / npm
packaged_at=$(date -Iseconds)
kind=$kind
admin_dist_files=$(find "$root/ISPmanager/frontend/dist" -type f | wc -l)
EOF

    if ! layout_is_split; then
        mkdir -p "$root/ISPweb/dist"
        cp -r "$PORTAL_FRONTEND_DIR/dist/." "$root/ISPweb/dist/"
        cat > "$root/ISPweb/.prebuilt-dist" <<EOF
# 预构建客户门户
packaged_at=$(date -Iseconds)
kind=$kind
portal_dist_files=$(find "$root/ISPweb/dist" -type f | wc -l)
EOF
    fi

    if layout_is_split; then
        python3 - "$root/ISPmanager/deploy/.env.example" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("DEPLOY_LAYOUT=combined", "DEPLOY_LAYOUT=split", 1)
path.write_text(text, encoding="utf-8")
PY
    fi

    cat > "$root/ISPmanager/deploy/VERSION" <<EOF
name=ispmanager
kind=$kind
release=$release_name
version=$APP_VERSION
packaged_at=$(date -Iseconds)
target_os=Ubuntu 22.04 / 24.04
https=nginx 443
python=3.12
layout=${DEPLOY_LAYOUT:-combined}
EOF

    local changelog
    if [[ "$kind" == "update" ]]; then
        changelog="v2.0.5.13 全新安装默认只允许 HTTPS，分机部署防火墙只对门户放行门户 API 端口，新增 Ubuntu 24.04 全新安装说明"
    else
        changelog="v2.0.5.13 Ubuntu 24.04 全新安装：MySQL/nginx/证书一键部署，只允许 HTTPS，支持公网 IP 访问，预留 API 与跨域配置"
    fi
    cat > "$root/ISPmanager/manifest.json" <<EOF
{
  "name": "ISPmanager",
  "type": "$kind",
  "version": "$APP_VERSION",
  "min_version": "1.1",
  "changelog": "$changelog"
}
EOF
    write_apply_sh "$root/ISPmanager/apply.sh"
    chmod +x "$root/ISPmanager/deploy/scripts/"*.sh
    find "$root/ISPmanager/deploy/scripts" -type f -name '*.sh' -exec sed -i 's/\r$//' {} \;
    normalize_release_modes "$root"
}

set_kind_metadata() {
    local root="$1" kind="$2" release_name="$3"
    local changelog
    if [[ "$kind" == "update" ]]; then
        changelog="v2.0.5.13 全新安装默认只允许 HTTPS，分机部署防火墙只对门户放行门户 API 端口，新增 Ubuntu 24.04 全新安装说明"
    else
        changelog="v2.0.5.13 Ubuntu 24.04 全新安装：MySQL/nginx/证书一键部署，只允许 HTTPS，支持公网 IP 访问，预留 API 与跨域配置"
    fi
    if layout_is_split; then
        python3 - "$root/ISPmanager/deploy/.env.example" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("DEPLOY_LAYOUT=combined", "DEPLOY_LAYOUT=split", 1)
path.write_text(text, encoding="utf-8")
PY
    fi

    cat > "$root/ISPmanager/deploy/VERSION" <<EOF
name=ispmanager
kind=$kind
release=$release_name
version=$APP_VERSION
packaged_at=$(date -Iseconds)
target_os=Ubuntu 22.04 / 24.04
https=nginx 443
python=3.12
layout=${DEPLOY_LAYOUT:-combined}
EOF
    cat > "$root/ISPmanager/manifest.json" <<EOF
{
  "name": "ISPmanager",
  "type": "$kind",
  "version": "$APP_VERSION",
  "min_version": "1.1",
  "changelog": "$changelog"
}
EOF
}

write_install_wrappers() {
    local root="$1"
    cat > "$root/install.sh" <<'WRAP'
#!/usr/bin/env bash
# 安装包入口：转到部署脚本执行一键安装
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/ISPmanager/deploy/scripts/install.sh" "$@"
WRAP
    chmod +x "$root/install.sh"
    cp -f "$DEPLOY_DIR/INSTALL.md" "$root/INSTALL.md"
    cp -f "$DEPLOY_DIR/INSTALL.md" "$root/QUICKSTART.md"
}

write_update_wrappers() {
    local root="$1"
    cat > "$root/update.sh" <<'WRAP'
#!/usr/bin/env bash
# 全量更新包入口：覆盖程序，保留业务数据
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PACKAGE_PAYLOAD="$ROOT"
exec bash "$ROOT/ISPmanager/deploy/scripts/update-from-package.sh" "$@"
WRAP
    chmod +x "$root/update.sh"
    cp -f "$DEPLOY_DIR/UPDATE.md" "$root/UPDATE.md"
}

make_archive() {
    local name="$1" src_dir="$2" dest="$3"
    tar -czf "$dest" -C "$(dirname "$src_dir")" "$name"
    if command -v sha256sum >/dev/null 2>&1; then
        ( cd "$(dirname "$dest")" && sha256sum "$(basename "$dest")" > "$dest.sha256" )
    fi
}

copy_stable() {
    local src="$1" dest="$2"
    rm -f "$dest" "$dest.sha256"
    cp -f "$src" "$dest"
    ( cd "$(dirname "$dest")" && sha256sum "$(basename "$dest")" > "$(basename "$dest").sha256" )
}

step "1/5 构建前端产物"
if [[ "$SKIP_BUILD" == "1" ]]; then
    log "SKIP_BUILD=1：跳过构建，复用现有 dist"
else
    command -v npm >/dev/null 2>&1 || die "未找到 npm，无法构建前端（或设 SKIP_BUILD=1 复用现有 dist）"
    specs=("管理端:$ADMIN_FRONTEND_DIR")
    if ! layout_is_split; then
        specs+=("客户门户:$PORTAL_FRONTEND_DIR")
    else
        log "分机部署：安装包不含客户门户，门户请用 ISPweb/deploy/pack.sh"
    fi
    for spec in "${specs[@]}"; do
        name="${spec%%:*}"
        d="${spec#*:}"
        [[ -d "$d" ]] || { warn "目录不存在，跳过：$d"; continue; }
        [[ -f "$d/package.json" ]] || die "$name 缺少 package.json：$d/package.json"
        [[ -f "$d/index.html" ]] || die "$name 缺少 index.html：$d/index.html"
        log "构建：$d"
        pushd "$d" >/dev/null
        if [[ "$name" == "管理端" ]]; then
            export ADMIN_BASE_PATH="${PACK_ADMIN_BASE:-/manager}"
            export VITE_BASE_PATH="${ADMIN_BASE_PATH}/"
            log "管理端生产 base：${VITE_BASE_PATH}"
        fi
        if [[ -f package-lock.json ]]; then npm ci || npm install; else npm install; fi
        npm run build
        popd >/dev/null
    done
fi

require_frontend_dist "管理端" "$ADMIN_FRONTEND_DIR"
require_admin_dist_base
if ! layout_is_split; then
    require_frontend_dist "客户门户" "$PORTAL_FRONTEND_DIR"
fi
ok "前端产物就绪"

APP_VERSION="$(read_app_version)"
INSTALL_NAME="ispmanager-install-${APP_VERSION}-$TS"
UPDATE_NAME="ispmanager-update-${APP_VERSION}-$TS"
INSTALL_ARCHIVE="$OUTPUT_DIR/$INSTALL_NAME.tar.gz"
UPDATE_ARCHIVE="$OUTPUT_DIR/$UPDATE_NAME.tar.gz"

step "2/5 收集公共程序文件"
stage_payload "$PAYLOAD_ROOT" "shared" "payload-$TS"
ok "公共载荷已就绪"

step "3/5 生成安装部署包"
INSTALL_ROOT="$STAGE/$INSTALL_NAME"
mkdir -p "$INSTALL_ROOT"
cp -a "$PAYLOAD_ROOT/." "$INSTALL_ROOT/"
set_kind_metadata "$INSTALL_ROOT" "install" "$INSTALL_NAME"
write_install_wrappers "$INSTALL_ROOT"
ok "安装包目录已生成"

step "4/5 生成全量更新包"
UPDATE_ROOT_DIR="$STAGE/$UPDATE_NAME"
mkdir -p "$UPDATE_ROOT_DIR"
cp -a "$PAYLOAD_ROOT/." "$UPDATE_ROOT_DIR/"
set_kind_metadata "$UPDATE_ROOT_DIR" "update" "$UPDATE_NAME"
write_update_wrappers "$UPDATE_ROOT_DIR"
ok "更新包目录已生成"

step "5/5 压缩并写校验和"
mkdir -p "$OUTPUT_DIR"
make_archive "$INSTALL_NAME" "$INSTALL_ROOT" "$INSTALL_ARCHIVE"
make_archive "$UPDATE_NAME" "$UPDATE_ROOT_DIR" "$UPDATE_ARCHIVE"

copy_stable "$INSTALL_ARCHIVE" "$OUTPUT_DIR/ispmanager-install-latest.tar.gz"
copy_stable "$UPDATE_ARCHIVE" "$OUTPUT_DIR/ispmanager-update-latest.tar.gz"
# 兼容旧文档/脚本：release-latest 指向安装包
copy_stable "$INSTALL_ARCHIVE" "$OUTPUT_DIR/ispmanager-release-latest.tar.gz"

INSTALL_SIZE="$(du -h "$INSTALL_ARCHIVE" | cut -f1)"
UPDATE_SIZE="$(du -h "$UPDATE_ARCHIVE" | cut -f1)"

echo
ok "════════════════════════════════════════════════════"
ok " 安装包与全量更新包已生成  (V${APP_VERSION})"
ok "════════════════════════════════════════════════════"
echo -e "  安装包： $INSTALL_ARCHIVE  ($INSTALL_SIZE)"
echo -e "  更新包： $UPDATE_ARCHIVE  ($UPDATE_SIZE)"
echo -e "  快捷名： $OUTPUT_DIR/ispmanager-install-latest.tar.gz"
echo -e "           $OUTPUT_DIR/ispmanager-update-latest.tar.gz"
echo
echo -e "  新装： sudo tar -xzf $(basename "$INSTALL_ARCHIVE") -C /opt/app --strip-components=1"
echo -e "        cd /opt/app && sudo SITE_DOMAIN=www.example.net bash install.sh"
echo
echo -e "  全量更新（不影响业务数据）："
echo -e "        sudo mkdir -p /tmp/isp-update && sudo tar -xzf $(basename "$UPDATE_ARCHIVE") -C /tmp/isp-update --strip-components=1"
echo -e "        sudo bash /tmp/isp-update/update.sh --root /opt/app"
