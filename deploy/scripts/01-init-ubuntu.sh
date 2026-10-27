#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# 01-init-ubuntu.sh — 初始化 Ubuntu 22.04 / 24.04 生产环境
#   安装 nginx / MySQL 8 / Python 3.12 / certbot
#   Node.js 仅在需要现场构建前端时安装（发布包可 SKIP_NODE=1）
# 用法：sudo bash 01-init-ubuntu.sh
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env
require_root

# ── MySQL 相关辅助函数 ──────────────────────────────────────────
mysql_installed_ok() {
    dpkg -s mysql-server-8.0 2>/dev/null | grep -q '^Status: install ok installed' \
        || dpkg -s mysql-server 2>/dev/null | grep -q '^Status: install ok installed'
}

ensure_mysql_running() {
    if has_systemd; then
        systemctl enable mysql 2>/dev/null || true
        systemctl start mysql 2>/dev/null || systemctl start mysql.service 2>/dev/null || true
    else
        service mysql start 2>/dev/null || {
            mkdir -p /var/run/mysqld
            chown mysql:mysql /var/run/mysqld 2>/dev/null || true
            (mysqld_safe --user=mysql >/dev/null 2>&1 &)
            sleep 5
        }
    fi
}

diagnose_mysql() {
    warn "───── MySQL 诊断信息 ─────"
    echo "· 内存："; free -h 2>/dev/null | sed 's/^/    /' || true
    echo "· 磁盘："; df -h / 2>/dev/null | sed 's/^/    /' || true
    echo "· systemd：$(has_systemd && echo 有 || echo '无（容器/WSL，postinst 可能无法启动服务）')"
    if [[ -f /var/log/mysql/error.log ]]; then
        echo "· /var/log/mysql/error.log 末尾："
        tail -n 20 /var/log/mysql/error.log 2>/dev/null | sed 's/^/    /'
    fi
    if has_systemd; then
        echo "· journalctl -u mysql 末尾："
        journalctl -u mysql -n 20 --no-pager 2>/dev/null | sed 's/^/    /' || true
    fi
    warn "──────────────────────────"
}

install_mysql() {
    if mysql_installed_ok; then
        log "MySQL 已安装，确保服务运行..."
        ensure_mysql_running
        ok "MySQL 已就绪"
        return 0
    fi

    if apt-get install -y mysql-server; then
        ensure_mysql_running
        ok "MySQL 安装并启动完成"
        return 0
    fi

    warn "MySQL 首次配置失败，进入自愈流程..."
    diagnose_mysql

    if [[ -d /var/lib/mysql ]]; then
        log "修复 /var/lib/mysql 属主为 mysql:mysql"
        chown -R mysql:mysql /var/lib/mysql 2>/dev/null || true
    fi

    ensure_mysql_running
    dpkg --configure -a || true
    apt-get install -f -y || true

    if mysql_installed_ok; then
        ensure_mysql_running
        ok "MySQL 已恢复并配置成功"
        return 0
    fi

    diagnose_mysql
    err "MySQL 自动修复失败。常见原因与处理："
    err "  1) 内存不足：MySQL 8 建议 ≥1GB 内存，可加 swap 后重试本脚本"
    err "  2) 无 systemd（容器）：postinst 无法启动服务"
    err "  3) 数据目录残留：sudo rm -rf /var/lib/mysql/* 后重试（会清空数据，谨慎）"
    die "MySQL 安装未完成"
}

install_python312() {
    if resolve_python; then
        ok "已有可用 Python：$("$PYTHON_BIN" --version 2>&1)"
        return 0
    fi

    log "安装 Python 3.12（Django 6 硬性要求）"
    if [[ "${OS_VERSION_ID:-}" == "24.04" ]]; then
        apt-get install -y python3 python3-venv python3-dev python3-pip \
            python3.12 python3.12-venv python3.12-dev 2>/dev/null \
            || apt-get install -y python3 python3-venv python3-dev
    else
        apt-get install -y software-properties-common
        add-apt-repository -y ppa:deadsnakes/ppa
        apt-get update -y
        apt-get install -y python3.12 python3.12-venv python3.12-dev
    fi

    resolve_python || die "安装后仍未找到 Python 3.12+，请检查 deadsnakes / apt 源"
    ok "Python 就绪：$("$PYTHON_BIN" --version 2>&1)"
}

step "1/8 检查系统版本"
OS_VERSION_ID=""
if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_VERSION_ID="${VERSION_ID:-}"
    log "检测到系统：$PRETTY_NAME"
    if [[ "${ID:-}" != "ubuntu" ]]; then
        warn "本脚本面向 Ubuntu 22.04 / 24.04，当前为 ${ID:-unknown}，继续尝试"
    elif [[ "$OS_VERSION_ID" != "22.04" && "$OS_VERSION_ID" != "24.04" ]]; then
        warn "官方验证版本为 Ubuntu 22.04 / 24.04，当前为 $OS_VERSION_ID"
    fi
fi

export DEBIAN_FRONTEND=noninteractive

step "2/8 修复中断的 dpkg 状态并更新 APT 源"
if dpkg --audit 2>/dev/null | grep -q .; then
    warn "检测到未完成配置的软件包，尝试修复 dpkg 状态..."
fi
dpkg --configure -a || true
apt-get install -f -y || true

apt-get update -y
apt-get install -y \
    ca-certificates curl wget gnupg lsb-release \
    build-essential pkg-config git rsync ufw openssl \
    dnsutils unzip tar sudo \
    python3 python3-venv python3-dev python3-pip \
    default-libmysqlclient-dev libssl-dev libffi-dev
apt-get install -y mysql-client \
    || apt-get install -y default-mysql-client \
    || apt-get install -y mysql-client-core-8.0 \
    || warn "mysql 客户端包未装上，后续 02-setup-database.sh 会再尝试"

step "3/8 安装 Python 3.12 与 mysqlclient 编译依赖"
install_python312

step "4/8 安装 Node.js 20（仅现场构建前端时需要）"
if [[ "${SKIP_NODE:-0}" == "1" ]]; then
    log "SKIP_NODE=1：跳过 Node.js（发布包已含前端 dist）"
elif both_frontends_prebuilt; then
    log "检测到预构建 dist，跳过 Node.js"
else
    if ! command -v node >/dev/null 2>&1 || [[ "$(node -v | cut -d. -f1 | tr -d v)" -lt 18 ]]; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
        apt-get install -y nodejs
    fi
    log "Node 版本：$(node -v)  npm 版本：$(npm -v)"
fi

step "5/8 安装 MySQL Server"
install_mysql

step "6/8 安装 nginx 与 certbot"
apt-get install -y nginx certbot python3-certbot-nginx
mkdir -p "$CERTBOT_WEBROOT"
if has_systemd; then
    systemctl enable --now nginx || true
else
    service nginx start 2>/dev/null || true
fi
ok "nginx 已安装并启动"

if [[ "${CHANNEL_LAYER_BACKEND:-}" == "redis" ]]; then
    step "6b/8 安装 Redis（CHANNEL_LAYER_BACKEND=redis）"
    apt-get install -y redis-server
    if has_systemd; then
        systemctl enable --now redis-server || systemctl enable --now redis || true
    fi
    ok "Redis 已安装"
fi

step "7/8 创建运行用户"
if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
    ok "已创建系统用户：$APP_USER"
else
    log "系统用户已存在：$APP_USER"
fi
getent group "$APP_GROUP" >/dev/null 2>&1 || groupadd --system "$APP_GROUP"
id "$APP_USER" >/dev/null 2>&1 && usermod -a -G "$APP_GROUP" "$APP_USER" 2>/dev/null || true

step "8/8 防火墙规则"
if command -v ufw >/dev/null 2>&1; then
    ufw allow OpenSSH >/dev/null 2>&1 || true
    ufw allow 'Nginx Full' >/dev/null 2>&1 || true
    log "已放行 ufw：OpenSSH / Nginx Full（如未启用 ufw 可忽略）"
fi

echo
ok "════════════════════════════════════════════════════"
ok " Ubuntu 初始化完成（Python 3.12 / nginx / MySQL / certbot）"
ok "════════════════════════════════════════════════════"
echo -e "  下一步：sudo bash 02-setup-database.sh"
echo -e "  或一键： sudo bash install.sh"
