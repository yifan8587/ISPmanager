#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# setup-ssl.sh — 为站点启用 HTTPS（不改写业务代码）
#   certbot certonly --webroot（失败则短时停 nginx 用 standalone）
#   不调用 certbot --nginx，避免覆盖站点。
#   申请成功后写入续期 hook + systemd timer / cron。
#   支持内网 NAT：公网 DNS 判断、不覆盖映射端口、80 未转发时不把 HTTP 跳死。
#
# 用法：
#   sudo bash setup-ssl.sh
#   sudo SSL_MODE=letsencrypt bash setup-ssl.sh
#   sudo SSL_MODE=selfsigned  bash setup-ssl.sh
#   sudo BEHIND_NAT=1 PUBLIC_IP=1.2.3.4 bash setup-ssl.sh
# ════════════════════════════════════════════════════════════════
# 必须在 load_env 之前保存：.env 里的 SSL_MODE=auto 会覆盖命令行
CLI_SSL_MODE="${SSL_MODE:-}"
CLI_CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
CLI_SITE_DOMAIN="${SITE_DOMAIN:-}"
CLI_SITE_ALIASES="${SITE_ALIASES:-}"
CLI_BEHIND_NAT="${BEHIND_NAT:-}"
CLI_PUBLIC_IP="${PUBLIC_IP:-}"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env
require_root

[[ -n "$CLI_SSL_MODE" ]] && export SSL_MODE="$CLI_SSL_MODE"
[[ -n "$CLI_CERTBOT_EMAIL" ]] && export CERTBOT_EMAIL="$CLI_CERTBOT_EMAIL"
[[ -n "$CLI_SITE_DOMAIN" ]] && export SITE_DOMAIN="$CLI_SITE_DOMAIN"
[[ -n "$CLI_SITE_ALIASES" ]] && export SITE_ALIASES="$CLI_SITE_ALIASES"
[[ -n "$CLI_BEHIND_NAT" ]] && export BEHIND_NAT="$CLI_BEHIND_NAT"
[[ -n "$CLI_PUBLIC_IP" ]] && export PUBLIC_IP="$CLI_PUBLIC_IP"
normalize_site_settings
apply_public_origins

command -v openssl >/dev/null 2>&1 || die "未找到 openssl"
command -v nginx >/dev/null 2>&1 || die "未找到 nginx，请先执行 01-init-ubuntu.sh"

if [[ $# -ge 1 ]]; then
    if [[ $# -ge 2 && "$1" != "$2" ]]; then
        warn "已改为单域名路径分流，忽略第二个参数 $2，使用 SITE_DOMAIN=$1"
    fi
    SITE_DOMAIN="$1"
    export SITE_DOMAIN
    normalize_site_settings
    apply_public_origins
fi

MODE="${SSL_MODE:-auto}"
EMAIL="${CERTBOT_EMAIL:-}"

[[ -n "$SITE_DOMAIN" ]] || die "未配置 SITE_DOMAIN"
normalize_publish_settings

is_example_domain() {
    [[ "$1" == *example.com || "$1" == *example.net || "$1" == *example.org ]]
}

# 命令替换只应拿到模式名；日志一律进 stderr
mode_log() { log "$@" >&2; }
mode_warn() { warn "$@" >&2; }

print_nat_hints() {
    local lan public_ip dns_ips
    lan="$(list_lan_ipv4 | head -n1 || true)"
    public_ip="$(detect_public_ip || true)"
    dns_ips="$(dns_a_records_public "$SITE_DOMAIN" 2>/dev/null | tr '\n' ' ' || true)"
    echo
    echo "  内网 NAT / 域名检查："
    echo "    本机内网： ${lan:-无}"
    echo "    出口公网： ${public_ip:-无法探测（可设 PUBLIC_IP=）}"
    echo "    域名公网A：${dns_ips:-无法解析（请用 8.8.8.8 查 A 记录）}"
    echo "    路由器转发：TCP ${PUBLIC_HTTP_PORT:-80} → ${lan:-内网IP}:${LISTEN_HTTP_PORT:-80}"
    echo "                 TCP ${PUBLIC_HTTPS_PORT:-443} → ${lan:-内网IP}:${LISTEN_HTTPS_PORT:-443}"
    echo "    本机访问公网 IP 失败属正常（多数路由无 NAT 回环），以路由器转发为准。"
    echo "    诊断： sudo bash $SCRIPT_DIR/check-nat-publish.sh"
}

# 只取最后一行有效模式，防止日志混进 MODE
normalize_ssl_mode() {
    local raw="${1:-}" last
    last="$(printf '%s\n' "$raw" | awk 'NF{line=$0} END{print line}')"
    last="${last//$'\r'/}"
    last="${last##* }"
    case "$last" in
        auto|letsencrypt|selfsigned|skip) echo "$last" ;;
        *) echo "$last" ;;
    esac
}

ensure_certbot() {
    if command -v certbot >/dev/null 2>&1; then
        return 0
    fi
    log "未找到 certbot，正在用 apt 安装免费客户端..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y certbot
    command -v certbot >/dev/null 2>&1 || die "certbot 安装失败，请检查 apt 源"
    ok "certbot 已安装：$(command -v certbot)"
}

prepare_acme_webroot() {
    mkdir -p "$CERTBOT_WEBROOT/.well-known/acme-challenge"
    chmod -R a+rX "$CERTBOT_WEBROOT"
    # 去掉默认站，避免 :80 被 default 抢走校验
    if [[ -e /etc/nginx/sites-enabled/default ]]; then
        rm -f /etc/nginx/sites-enabled/default
        log "已禁用 nginx default 站点，保证 ACME 落到本站"
    fi
}

acme_local_ready() {
    local token="_isp-preflight-$$"
    local dir="$CERTBOT_WEBROOT/.well-known/acme-challenge"
    local url="http://127.0.0.1:${LISTEN_HTTP_PORT:-80}/.well-known/acme-challenge/${token}"
    mkdir -p "$dir"
    chmod a+rX "$CERTBOT_WEBROOT" "$CERTBOT_WEBROOT/.well-known" "$dir" 2>/dev/null || true
    echo "ok" > "$dir/$token"
    local body
    body="$(curl -fsS --max-time 5 -H "Host: ${SITE_DOMAIN}" "$url" 2>/dev/null || true)"
    rm -f "$dir/$token"
    [[ "$body" == "ok" ]]
}

wait_acme_local() {
    local i
    for i in 1 2 3 4 5 6; do
        if acme_local_ready; then
            return 0
        fi
        sleep 1
    done
    return 1
}

ensure_nginx_http_for_acme() {
    prepare_acme_webroot
    [[ -f "$NGINX_AVAIL" ]] || die "尚未部署 nginx 站点，请先 sudo bash deploy.sh"
    local saved_ssl_redirect="${SSL_HTTP_REDIRECT:-}"
    # 申请前不要 301 到 HTTPS，避免校验被跳走（尤其自签/未就绪证书）
    SSL_HTTP_REDIRECT=0
    render_nginx_conf "$NGINX_AVAIL"
    render_portal_api_nginx
    enable_nginx_site
    if has_systemd; then
        systemctl enable nginx >/dev/null 2>&1 || true
        systemctl start nginx || true
    fi
    reload_nginx || {
        warn "nginx -t/reload 失败，继续尝试申请（standalone 可不依赖本机 ACME）"
    }
    SSL_HTTP_REDIRECT="$saved_ssl_redirect"
}

collect_certbot_domains() {
    local args=() alias
    args=(-d "$SITE_DOMAIN")
    for alias in ${SITE_ALIASES:-}; do
        [[ -n "$alias" ]] || continue
        is_ipv4 "$alias" && continue
        is_example_domain "$alias" && continue
        args+=(-d "$alias")
    done
    printf '%s\n' "${args[@]}"
}

certbot_mail_args() {
    if [[ -n "$EMAIL" ]] && ! is_placeholder "$EMAIL" email; then
        printf '%s\n' --email "$EMAIL" --no-eff-email
    else
        warn "CERTBOT_EMAIL 未配置，使用 --register-unsafely-without-email"
        printf '%s\n' --register-unsafely-without-email
    fi
}

run_certbot() {
    local plugin="$1"
    shift
    local -a domain_args=() mail_args=()
    mapfile -t domain_args < <(collect_certbot_domains)
    mapfile -t mail_args < <(certbot_mail_args)
    log "certbot ${plugin} 申请：${domain_args[*]}"
    # 不用 --nginx，避免改写站点。成功后证书在 /etc/letsencrypt/live/<cert-name>/
    certbot certonly "$plugin" \
        "$@" \
        "${domain_args[@]}" \
        "${mail_args[@]}" \
        --agree-tos \
        --non-interactive \
        --keep-until-expiring \
        --preferred-challenges http \
        --cert-name "$SITE_DOMAIN"
}

issue_letsencrypt_webroot() {
    run_certbot --webroot -w "$CERTBOT_WEBROOT"
}

issue_letsencrypt_standalone() {
    local was_active=0
    if has_systemd && systemctl is-active --quiet nginx; then
        was_active=1
        log "webroot 不可用或失败，短时停止 nginx，改用 standalone 占用 :${LISTEN_HTTP_PORT:-80}"
        systemctl stop nginx
    elif command -v nginx >/dev/null 2>&1; then
        was_active=1
        nginx -s stop 2>/dev/null || true
    fi
    local rc=0
    run_certbot --standalone --http-01-port "${LISTEN_HTTP_PORT:-80}" || rc=$?
    if [[ "$was_active" == "1" ]]; then
        if has_systemd; then
            systemctl start nginx || true
        else
            nginx || true
        fi
    fi
    return "$rc"
}

issue_letsencrypt() {
    ensure_certbot
    prepare_acme_webroot
    ensure_nginx_http_for_acme

    if wait_acme_local; then
        ok "本机 ACME 路径已就绪，使用 webroot 申请"
        if issue_letsencrypt_webroot; then
            return 0
        fi
        warn "webroot 申请失败（多为公网 80 未转发或校验文件未到 Let's Encrypt），改试 standalone"
    else
        warn "本机 http://127.0.0.1:${LISTEN_HTTP_PORT:-80}/.well-known/acme-challenge/ 不可用，改试 standalone"
        print_nat_hints
    fi

    if issue_letsencrypt_standalone; then
        return 0
    fi

    # 别名可能导致整单失败，再只用主域名试一次
    if [[ -n "${SITE_ALIASES:-}" ]]; then
        warn "带 SITE_ALIASES 申请失败，仅用 ${SITE_DOMAIN} 再试一次"
        local saved_aliases="${SITE_ALIASES}"
        SITE_ALIASES=""
        local rc=0
        if wait_acme_local && issue_letsencrypt_webroot; then
            SITE_ALIASES="$saved_aliases"
            return 0
        fi
        issue_letsencrypt_standalone || rc=$?
        SITE_ALIASES="$saved_aliases"
        [[ "$rc" -eq 0 ]] && return 0
    fi

    err "Let's Encrypt 申请失败。请确认："
    err "  1) ${SITE_DOMAIN} 公网 A 指向出口 IP"
    err "  2) 路由器转发 TCP 80 → 本机 ${LISTEN_HTTP_PORT:-80}"
    err "  3) 本机防火墙放行 ${LISTEN_HTTP_PORT:-80}/tcp"
    print_nat_hints
    return 1
}

issue_selfsigned() {
    mkdir -p "$SELF_SIGNED_DIR"
    local key="$SELF_SIGNED_DIR/privkey.pem"
    local crt="$SELF_SIGNED_DIR/fullchain.pem"
    local conf="$SELF_SIGNED_DIR/san.cnf"
    local dns_lines="" ip_lines=""
    local dns_idx=1 ip_idx=1
    local item

    add_dns() {
        local name="$1"
        [[ -n "$name" ]] || return 0
        is_ipv4 "$name" && return 0
        dns_lines="${dns_lines}"$'\n'"DNS.${dns_idx} = ${name}"
        dns_idx=$((dns_idx + 1))
    }
    add_ip() {
        local ip="$1"
        [[ -n "$ip" ]] && is_ipv4 "$ip" || return 0
        ip_lines="${ip_lines}"$'\n'"IP.${ip_idx} = ${ip}"
        ip_idx=$((ip_idx + 1))
    }

    add_dns "$SITE_DOMAIN"
    add_ip "$SITE_DOMAIN"
    for item in ${SITE_ALIASES:-}; do
        add_dns "$item"
        add_ip "$item"
    done
    for item in $(list_lan_ipv4); do
        add_ip "$item"
    done
    add_ip "${PUBLIC_IP:-}"
    add_ip 127.0.0.1
    if item="$(detect_public_ip || true)"; then
        add_ip "$item"
    fi
    [[ -n "$dns_lines" ]] || dns_lines=$'\n'"DNS.1 = localhost"

    cat > "$conf" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = ${SITE_DOMAIN}

[v3_req]
subjectAltName = @alt_names
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]${dns_lines}${ip_lines}
EOF
    openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
        -keyout "$key" -out "$crt" -config "$conf"
    chmod 640 "$key"
    chmod 644 "$crt"
    chown root:"$APP_GROUP" "$key" "$crt" 2>/dev/null || true
    ok "已生成自签证书（含内网 IP / 公网 IP SAN）。浏览器告警属预期。"
}

persist_publish_env() {
    local env_file="$DEPLOY_DIR/.env"
    [[ -f "$env_file" ]] || return 0
    upsert_env_file "$env_file" SITE_DOMAIN "$SITE_DOMAIN"
    upsert_env_file "$env_file" ADMIN_DOMAIN "$SITE_DOMAIN"
    upsert_env_file "$env_file" DEPLOY_LAYOUT "${DEPLOY_LAYOUT:-combined}"
    if layout_is_split; then
        upsert_env_file "$env_file" PORTAL_DOMAIN "$PORTAL_DOMAIN"
        upsert_env_file "$env_file" PORTAL_PUBLIC_ORIGIN "$PORTAL_PUBLIC_ORIGIN"
    else
        upsert_env_file "$env_file" PORTAL_DOMAIN "$SITE_DOMAIN"
    fi
    upsert_env_file "$env_file" ADMIN_BASE_PATH "$ADMIN_BASE_PATH"
    upsert_env_file "$env_file" DJANGO_SECURE_PROXY_SSL_HEADER True
    upsert_env_file "$env_file" PUBLIC_PORT "$PUBLIC_PORT"
    upsert_env_file "$env_file" PUBLIC_HTTP_PORT "$PUBLIC_HTTP_PORT"
    upsert_env_file "$env_file" PUBLIC_HTTPS_PORT "$PUBLIC_HTTPS_PORT"
    upsert_env_file "$env_file" ADMIN_PUBLIC_PORT "$ADMIN_PUBLIC_PORT"
    upsert_env_file "$env_file" PORTAL_PUBLIC_PORT "$PORTAL_PUBLIC_PORT"
    upsert_env_file "$env_file" LISTEN_HTTP_PORT "$LISTEN_HTTP_PORT"
    upsert_env_file "$env_file" LISTEN_HTTPS_PORT "$LISTEN_HTTPS_PORT"
    upsert_env_file "$env_file" BEHIND_NAT "$BEHIND_NAT"
    upsert_env_file "$env_file" PUBLIC_IP "${PUBLIC_IP:-}"
    upsert_env_file "$env_file" SSL_HTTP_REDIRECT "$SSL_HTTP_REDIRECT"
    upsert_env_file "$env_file" SSL_MODE "$MODE"
    upsert_env_file "$env_file" CERTBOT_EMAIL "${EMAIL:-}"
    upsert_env_file "$env_file" PAYMENT_FRONTEND_URL "$PAYMENT_FRONTEND_URL"
    upsert_env_file "$env_file" DJANGO_CSRF_TRUSTED_ORIGINS "$DJANGO_CSRF_TRUSTED_ORIGINS"
    upsert_env_file "$env_file" DJANGO_CORS_ALLOWED_ORIGINS "$DJANGO_CORS_ALLOWED_ORIGINS"
}

enable_https_runtime() {
    export DJANGO_SECURE_PROXY_SSL_HEADER=True
    # 不把映射端口强行改成 443：NAT / 面板映射必须保留 PUBLIC_HTTPS_PORT
    if [[ -z "${PUBLIC_HTTPS_PORT:-}" || "${PUBLIC_HTTPS_PORT}" == "80" ]]; then
        if [[ -n "${PUBLIC_PORT:-}" && "${PUBLIC_PORT}" != "80" ]]; then
            PUBLIC_HTTPS_PORT="$PUBLIC_PORT"
        else
            PUBLIC_HTTPS_PORT=443
        fi
    fi
    export PUBLIC_HTTPS_PORT
    export PUBLIC_HTTP_PORT="${PUBLIC_HTTP_PORT:-80}"
    export PUBLIC_PORT="$PUBLIC_HTTPS_PORT"
    export ADMIN_PUBLIC_PORT="$PUBLIC_PORT"
    if ! layout_is_split; then
        export PORTAL_PUBLIC_PORT="$PUBLIC_PORT"
    fi
    SSL_HTTP_REDIRECT="$(resolve_ssl_http_redirect)"
    export SSL_HTTP_REDIRECT
    ensure_payment_frontend_url https
    apply_public_origins
    write_runtime_env "$RUNTIME_ENV_FILE"
    persist_publish_env

    if has_systemd && systemctl is-enabled ispmanager.service >/dev/null 2>&1; then
        systemctl restart ispmanager.service
        sleep 1
        systemctl is-active --quiet ispmanager.service || {
            journalctl -u ispmanager -n 20 --no-pager || true
            die "启用 HTTPS 后后端重启失败"
        }
    fi
}

install_auto_renewal() {
    ssl_is_letsencrypt || return 0
    mkdir -p /etc/letsencrypt/renewal-hooks/deploy
    cat > /etc/letsencrypt/renewal-hooks/deploy/ispmanager-reload.sh <<'HOOK'
#!/usr/bin/env bash
# certbot 续期成功后重载本站，门户 /、/manager、/admin、/api、/ws 立刻用新证书
set -e
if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then
    if command -v systemctl >/dev/null 2>&1; then
        systemctl reload nginx 2>/dev/null || nginx -s reload || true
    else
        nginx -s reload || true
    fi
fi
if command -v systemctl >/dev/null 2>&1 && systemctl is-enabled ispmanager.service >/dev/null 2>&1; then
    systemctl reload ispmanager.service 2>/dev/null || \
        systemctl restart ispmanager.service 2>/dev/null || true
fi
HOOK
    chmod 755 /etc/letsencrypt/renewal-hooks/deploy/ispmanager-reload.sh
    ok "已写入续期 hook：/etc/letsencrypt/renewal-hooks/deploy/ispmanager-reload.sh"

    if has_systemd && systemctl list-unit-files --type=timer 2>/dev/null | grep -q '^certbot.timer'; then
        systemctl enable --now certbot.timer
        ok "已启用 certbot.timer（每天两次尝试续期，距到期 30 天才真正换证）"
        return 0
    fi

    if has_systemd; then
        cat > /etc/systemd/system/ispmanager-certbot-renew.service <<'UNIT'
[Unit]
Description=Renew ISPmanager Let's Encrypt certificates
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/certbot renew --quiet
UNIT
        cat > /etc/systemd/system/ispmanager-certbot-renew.timer <<'UNIT'
[Unit]
Description=Twice-daily Let's Encrypt renew for ISPmanager

[Timer]
OnCalendar=*-*-* 03,15:17:00
RandomizedDelaySec=3600
Persistent=true

[Install]
WantedBy=timers.target
UNIT
        systemctl daemon-reload
        systemctl enable --now ispmanager-certbot-renew.timer
        ok "已启用 ispmanager-certbot-renew.timer"
        return 0
    fi

    if [[ -d /etc/cron.d ]]; then
        cat > /etc/cron.d/ispmanager-certbot <<'CRON'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
17 3,15 * * * root /usr/bin/certbot renew --quiet
CRON
        chmod 644 /etc/cron.d/ispmanager-certbot
        ok "已写入 /etc/cron.d/ispmanager-certbot（每天 03:17 / 15:17）"
    fi
}

decide_auto_mode() {
    if is_example_domain "$SITE_DOMAIN"; then
        mode_warn "域名为 example.*，自动改用自签证书"
        echo selfsigned
        return
    fi
    if is_ipv4 "$SITE_DOMAIN"; then
        mode_warn "SITE_DOMAIN 是 IP，Let's Encrypt 不能签发，改用自签"
        echo selfsigned
        return
    fi
    if [[ "$SITE_DOMAIN" == *.local || "$SITE_DOMAIN" == *.lan ]]; then
        mode_warn "内网专用域名，改用自签证书"
        echo selfsigned
        return
    fi

    local public_ip dns_ips
    public_ip="$(detect_public_ip || true)"
    dns_ips="$(dns_a_records_public "$SITE_DOMAIN" 2>/dev/null || true)"
    [[ -n "$public_ip" ]] && mode_log "出口公网 IP：$public_ip"
    [[ -n "$dns_ips" ]] && mode_log "域名公网 A：$(echo "$dns_ips" | tr '\n' ' ')"

    if [[ -n "$dns_ips" && -n "$public_ip" ]] && domain_points_here "$SITE_DOMAIN" "$public_ip"; then
        mode_log "公网 DNS 已指向本机出口 IP，尝试 Let's Encrypt"
        echo letsencrypt
        return
    fi

    if [[ "${BEHIND_NAT:-0}" == "1" && -n "$dns_ips" ]]; then
        local first
        first="$(echo "$dns_ips" | awk 'NR==1{print}')"
        if [[ -n "$first" ]] && ! is_private_ipv4 "$first"; then
            mode_warn "内网 NAT：域名 A 为公网地址但与出口 IP 不完全一致（多线/hosts），仍尝试 Let's Encrypt"
            echo letsencrypt
            return
        fi
    fi

    mode_warn "DNS 尚未确认指向本机，改用自签。解析并转发 80/443 后执行：SSL_MODE=letsencrypt sudo bash setup-ssl.sh"
    print_nat_hints >&2
    echo selfsigned
}

step "选择证书模式（当前 SSL_MODE=$MODE，BEHIND_NAT=${BEHIND_NAT:-0}）"
case "$MODE" in
    skip)
        log "SSL_MODE=skip，保持 HTTP"
        persist_publish_env
        exit 0
        ;;
    auto)
        MODE="$(normalize_ssl_mode "$(decide_auto_mode)")"
        log "auto 决策结果：$MODE"
        ;;
    letsencrypt|selfsigned)
        log "按命令行/环境强制使用 $MODE"
        ;;
    *)
        die "未知 SSL_MODE=$MODE（可用 auto|letsencrypt|selfsigned|skip）"
        ;;
esac
MODE="$(normalize_ssl_mode "$MODE")"

ensure_listen_firewall

if [[ "${SKIP_CERT_ISSUE:-0}" == "1" ]] && ssl_material_ready; then
    log "证书已存在，跳过签发，仅重渲染 HTTPS 配置"
    enable_https_runtime
    render_nginx_conf "$NGINX_AVAIL"
    render_portal_api_nginx
    enable_nginx_site
    reload_nginx
    install_auto_renewal
    ok "已沿用现有证书并刷新 nginx"
    print_site_urls https
    exit 0
fi

if [[ "$MODE" == "letsencrypt" ]]; then
    if is_example_domain "$SITE_DOMAIN" || is_ipv4 "$SITE_DOMAIN"; then
        die "Let's Encrypt 不能使用 example.* 或纯 IP，请改真实域名或 SSL_MODE=selfsigned"
    fi
    if [[ ! -f "$NGINX_AVAIL" ]]; then
        die "尚未部署 nginx 站点，请先 sudo bash deploy.sh"
    fi
    if ! issue_letsencrypt; then
        if [[ "${SSL_MODE:-auto}" == "auto" ]]; then
            warn "Let's Encrypt 失败，回退自签。常见原因：路由器未转发 80，或域名 A 未指向公网 IP。"
            print_nat_hints
            MODE=selfsigned
        else
            print_nat_hints
            die "Let's Encrypt 申请失败。路由器需转发 80→本机 ${LISTEN_HTTP_PORT:-80}，且 ${SITE_DOMAIN} 的公网 A 指向出口 IP。"
        fi
    fi
fi

if [[ "$MODE" == "selfsigned" ]]; then
    issue_selfsigned
fi

if ! ssl_material_ready; then
    err "期望证书：/etc/letsencrypt/live/${SITE_DOMAIN}/fullchain.pem"
    err "或自签：  $SELF_SIGNED_DIR/fullchain.pem"
    ls -la "/etc/letsencrypt/live/${SITE_DOMAIN}/" 2>/dev/null || true
    ls -la "$SELF_SIGNED_DIR/" 2>/dev/null || true
    die "证书文件未就绪，无法启用 HTTPS"
fi

step "渲染 HTTPS nginx 并更新 Django 反代头"
enable_https_runtime
render_nginx_conf "$NGINX_AVAIL"
render_portal_api_nginx
enable_nginx_site
reload_nginx
if layout_is_split; then
    ok "nginx 已应用 HTTPS（管理端 /manager、/admin/、管理 API，以及门户 API 端口）"
else
    ok "nginx 已应用 HTTPS（/、/manager、/admin/、/api/、/ws/）"
fi

install_auto_renewal

echo
ok "════════════════════════════════════════════════════"
ok " HTTPS 已启用"
ok "════════════════════════════════════════════════════"
print_site_urls https
if ssl_is_letsencrypt; then
    echo -e "  证书：     Let's Encrypt  /etc/letsencrypt/live/${SITE_DOMAIN}/"
    echo -e "  续期：     certbot.timer 或 ispmanager-certbot-renew.timer（到期前自动换证并 reload nginx）"
    echo -e "  手动续期： sudo certbot renew --dry-run"
else
    echo -e "  证书：     自签（浏览器告警属预期）"
    if [[ "$(resolve_ssl_http_redirect)" != "1" ]]; then
        echo -e "  HTTP：     内网 NAT 未跳转 HTTPS，公网仍可用 http://${SITE_DOMAIN}/"
    fi
    echo -e "  换公网证书：sudo SSL_MODE=letsencrypt bash setup-ssl.sh"
fi
