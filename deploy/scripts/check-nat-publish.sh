#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# check-nat-publish.sh — 内网 NAT / 域名 / SSL 发布诊断
# 用法：sudo bash check-nat-publish.sh
# ════════════════════════════════════════════════════════════════
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
load_env

step "本机地址"
LAN_IPS="$(list_lan_ipv4 | tr '\n' ' ')"
PUBLIC="$(detect_public_ip || true)"
echo "  内网 IPv4： ${LAN_IPS:-无}"
echo "  出口公网： ${PUBLIC:-无法探测（可在 .env 写 PUBLIC_IP=）}"
echo "  BEHIND_NAT=${BEHIND_NAT:-0}"

step "域名公网解析（绕过 /etc/hosts）"
if is_ipv4 "$SITE_DOMAIN"; then
    warn "SITE_DOMAIN 是 IP，不能申请 Let's Encrypt"
else
    DNS="$(dns_a_records_public "$SITE_DOMAIN" 2>/dev/null || true)"
    HOSTS="$(getent ahostsv4 "$SITE_DOMAIN" 2>/dev/null | awk '{print $1; exit}' || true)"
    echo "  SITE_DOMAIN     = $SITE_DOMAIN"
    echo "  公网 A @8.8.8.8 = ${DNS:-无}"
    echo "  本机 getent     = ${HOSTS:-无}（若是内网 IP，说明 /etc/hosts 覆盖了域名）"
    if [[ -n "$DNS" && -n "$PUBLIC" ]] && domain_points_here "$SITE_DOMAIN" "$PUBLIC"; then
        ok "公网 DNS 已指向出口 IP"
    else
        warn "公网 DNS 尚未指向出口 IP，Let's Encrypt HTTP-01 会失败"
    fi
fi

step "监听与映射"
echo "  nginx 监听 HTTP  = ${LISTEN_HTTP_PORT:-80}"
echo "  nginx 监听 HTTPS = ${LISTEN_HTTPS_PORT:-443}"
echo "  浏览器 HTTP 端口 = ${PUBLIC_HTTP_PORT:-80}"
echo "  浏览器 HTTPS 端口= ${PUBLIC_HTTPS_PORT:-443}"
echo "  CSRF PUBLIC_PORT = ${PUBLIC_PORT:-}"
echo
echo "  路由器应转发："
echo "    公网 TCP ${PUBLIC_HTTP_PORT:-80}  → ${LAN_IPS:-内网IP}${LAN_IPS:+ }${LISTEN_HTTP_PORT:-80}"
echo "    公网 TCP ${PUBLIC_HTTPS_PORT:-443} → ${LAN_IPS:-内网IP}${LAN_IPS:+ }${LISTEN_HTTPS_PORT:-443}"

step "本机 ACME / 站点"
if ss -lnt 2>/dev/null | grep -q ":${LISTEN_HTTP_PORT:-80} " || \
   netstat -lnt 2>/dev/null | grep -q ":${LISTEN_HTTP_PORT:-80} "; then
    ok "本机已监听 ${LISTEN_HTTP_PORT:-80}"
else
    warn "本机未监听 ${LISTEN_HTTP_PORT:-80}，先 deploy.sh / 启动 nginx"
fi

TOKEN_DIR="${CERTBOT_WEBROOT:-/var/www/certbot}/.well-known/acme-challenge"
mkdir -p "$TOKEN_DIR" 2>/dev/null || true
echo "ok" > "$TOKEN_DIR/_isp-check" 2>/dev/null || true
BODY="$(curl -fsS --max-time 5 -H "Host: ${SITE_DOMAIN}" \
    "http://127.0.0.1:${LISTEN_HTTP_PORT:-80}/.well-known/acme-challenge/_isp-check" 2>/dev/null || true)"
rm -f "$TOKEN_DIR/_isp-check" 2>/dev/null || true
if [[ "$BODY" == "ok" ]]; then
    ok "本机 ACME 路径可用（Let's Encrypt 本机侧就绪）"
else
    warn "本机 ACME 路径不可用。先保证 nginx HTTP 站点含 /.well-known/acme-challenge/"
fi

if [[ -n "$PUBLIC" ]]; then
    HAIR="$(curl -fsS --max-time 5 -H "Host: ${SITE_DOMAIN}" \
        "http://${PUBLIC}/.well-known/acme-challenge/_no-such" 2>/dev/null || true)"
    if [[ -z "$HAIR" ]]; then
        log "从本机访问公网 IP:80 无响应：多数 NAT 无回环，不能据此判断路由器未转发"
    fi
fi

step "证书"
if ssl_is_letsencrypt; then
    ok "Let's Encrypt 证书已存在"
elif ssl_material_ready; then
    warn "当前为自签证书。80 转发且 DNS 就绪后：sudo SSL_MODE=letsencrypt bash setup-ssl.sh"
else
    warn "尚未签发证书"
fi

echo
ok "诊断完成。申请公网证书：sudo SSL_MODE=letsencrypt bash $SCRIPT_DIR/setup-ssl.sh"
