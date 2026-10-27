#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# 02-setup-database.sh — 创建 MySQL 数据库与应用账号
#   读取 .env 中的 DB_NAME / DB_USER / DB_PASSWORD / MYSQL_ROOT_PASSWORD
# 用法：sudo bash 02-setup-database.sh
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env
require_root

[[ -n "${DB_NAME:-}" && -n "${DB_USER:-}" && -n "${DB_PASSWORD:-}" ]] || \
    die "请在 deploy/.env 中设置 DB_NAME / DB_USER / DB_PASSWORD"

if is_placeholder "$DB_PASSWORD"; then
    die "DB_PASSWORD 仍是占位符，请先填写或运行 install.sh 自动生成"
fi

if db_app_connects; then
    ok "应用账号已能连接 $DB_NAME，跳过建库/改密（升级安全）"
    exit 0
fi

step "创建数据库与账号"
log "数据库：$DB_NAME    账号：$DB_USER@$DB_HOST"

MYSQL_ARGS=(--protocol=socket)
if [[ -n "${MYSQL_ROOT_PASSWORD:-}" ]] && ! is_placeholder "$MYSQL_ROOT_PASSWORD"; then
    MYSQL_ARGS=(-u root -p"${MYSQL_ROOT_PASSWORD}")
fi

SQL=$(cat <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\`
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
CREATE USER IF NOT EXISTS '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
)

if echo "$SQL" | mysql "${MYSQL_ARGS[@]}" 2>/dev/null; then
    ok "数据库与账号创建/更新成功"
else
    warn "使用给定凭据连接失败，尝试 sudo mysql（auth_socket）..."
    echo "$SQL" | mysql --protocol=socket
    ok "数据库与账号创建/更新成功（socket 模式）"
fi

step "连接验证"
if mysql -u"$DB_USER" -p"$DB_PASSWORD" -h"$DB_HOST" -P"${DB_PORT:-3306}" \
    -e "USE \`$DB_NAME\`; SELECT 1;" >/dev/null 2>&1; then
    ok "应用账号可正常连接数据库 $DB_NAME"
else
    die "应用账号连接失败，请检查 .env 中数据库配置"
fi
