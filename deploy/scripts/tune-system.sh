#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# tune-system.sh — 按机器规格调优 MySQL / nginx / systemd / 内核
#   升级与全新安装均可重复执行（幂等）
# 用法：sudo bash tune-system.sh
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env
require_root

RAM_MB="$(mem_total_mb)"
CPU_N="$(cpu_count)"
WORKERS="$(recommended_gunicorn_workers)"
SWAP_MB="$(awk '/SwapTotal:/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)"

INNODB_MB=$((RAM_MB / 4))
[[ "$INNODB_MB" -lt 128 ]] && INNODB_MB=128
[[ "$INNODB_MB" -gt 1024 ]] && INNODB_MB=1024
[[ "$RAM_MB" -lt 1200 ]] && INNODB_MB=128
MAX_CONN=80
[[ "$RAM_MB" -ge 3000 ]] && MAX_CONN=150

step "系统调优（内存 ${RAM_MB}MB · CPU ${CPU_N}）"

if [[ "${TUNE_WORKERS:-1}" == "1" ]]; then
    export GUNICORN_WORKERS="$WORKERS"
    if [[ -f "$DEPLOY_DIR/.env" ]]; then
        upsert_env_file "$DEPLOY_DIR/.env" GUNICORN_WORKERS "$WORKERS"
    fi
    ok "gunicorn workers → $WORKERS"
fi

step "补齐 swap（低内存机器）"
if [[ "$RAM_MB" -lt 2048 && "${SWAP_MB:-0}" -lt 256 && "${SKIP_SWAP:-0}" != "1" ]]; then
    if [[ ! -f /swapfile ]]; then
        log "内存 < 2G 且无 swap，创建 2G /swapfile"
        fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
        chmod 600 /swapfile
        mkswap /swapfile >/dev/null
        swapon /swapfile
        grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
        ok "已启用 2G swap"
    else
        swapon /swapfile 2>/dev/null || true
        log "已存在 /swapfile"
    fi
else
    log "跳过 swap（内存 ${RAM_MB}MB / 已有 swap ${SWAP_MB}MB）"
fi

step "内核与文件句柄"
cat > /etc/sysctl.d/99-ispmanager.conf <<EOF
# ISPmanager 部署调优（可重复覆盖）
vm.swappiness=10
net.core.somaxconn=1024
net.ipv4.tcp_fin_timeout=30
fs.file-max=2097152
EOF
sysctl -p /etc/sysctl.d/99-ispmanager.conf >/dev/null 2>&1 || true

cat > /etc/security/limits.d/ispmanager.conf <<EOF
${APP_USER} soft nofile 65535
${APP_USER} hard nofile 65535
${APP_USER} soft nproc 4096
${APP_USER} hard nproc 4096
root soft nofile 65535
root hard nofile 65535
EOF
ok "已写入 sysctl / limits"

step "systemd 句柄与重启策略"
mkdir -p /etc/systemd/system/ispmanager.service.d
cat > /etc/systemd/system/ispmanager.service.d/override.conf <<EOF
[Service]
LimitNOFILE=65535
LimitNPROC=4096
Restart=always
RestartSec=3
TimeoutStopSec=45
EOF
has_systemd && systemctl daemon-reload || true
ok "已写入 ispmanager.service.d/override.conf"

step "nginx 连接调优"
mkdir -p /etc/nginx/conf.d
cat > /etc/nginx/conf.d/ispmanager-tune.conf <<'EOF'
# ISPmanager：与主站点模板并存，不改 server 块
keepalive_timeout 65;
keepalive_requests 1000;
EOF
if nginx -t >/dev/null 2>&1; then
    ok "nginx 调优片段已写入"
else
    warn "nginx -t 未通过，已保留片段，请在站点渲染后再 reload"
fi

step "MySQL InnoDB 调优"
if command -v mysqld >/dev/null 2>&1 || dpkg -s mysql-server >/dev/null 2>&1; then
    mkdir -p /etc/mysql/mysql.conf.d
    cat > /etc/mysql/mysql.conf.d/ispmanager-tune.cnf <<EOF
[mysqld]
# ISPmanager 按本机内存生成，可重复执行覆盖
innodb_buffer_pool_size = ${INNODB_MB}M
innodb_log_file_size = 64M
innodb_flush_method = O_DIRECT
max_connections = ${MAX_CONN}
table_open_cache = 400
skip_name_resolve = ON
EOF
    if has_systemd && systemctl is-active --quiet mysql 2>/dev/null; then
        log "重载 MySQL 使调优生效（短暂中断连接）"
        systemctl restart mysql || warn "MySQL 重启失败，配置已写入，稍后手工 restart mysql"
    fi
    ok "InnoDB buffer_pool=${INNODB_MB}M  max_connections=${MAX_CONN}"
else
    log "未安装 MySQL，跳过库内调优"
fi

echo
ok "系统调优完成（workers=$WORKERS innodb=${INNODB_MB}M）"
