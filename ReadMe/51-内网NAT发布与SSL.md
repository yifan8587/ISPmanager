# 内网 NAT 发布、域名与 SSL

**日期**：2026-10-04  
**版本**：`2.0.1`  
**范围**：`deploy/scripts/setup-ssl.sh`、`common.sh`、nginx 模板、`check-nat-publish.sh`

适用于服务器在路由器后面（只有 192.168/10/172.16 地址），用域名 + 端口转发对外发布门户 `/`、管理端 `/manager`、Django Admin `/admin/`。

发布包（`ISPmanager/deploy/`）：

| 文件 | SHA256 |
|------|--------|
| `ispmanager-install-2.0.1-20261004-143355.tar.gz` | `1a8911a9e34f50f61960793fe0af93576d260256b41e245b0e04cd077fc95d90` |
| `ispmanager-update-2.0.1-20261004-143355.tar.gz` | `94732ab062f8f1be0f8cc3ebfff14a9edcb0daa922ccdb5eeda589c21becd7d2` |

---

## 一、典型拓扑

```
浏览器 ──► 公网 IP:80/443 ──DNAT──► 内网 192.168.x.x:80/443 nginx
                ▲
         域名 A 记录指向公网 IP
```

本机 **不要** 在 `/etc/hosts` 把站点域名写成内网 IP，否则旧脚本会误判「DNS 未指向本机」而跳过 Let's Encrypt。2.0.1 起域名解析改走 `8.8.8.8` / `1.1.1.1`。

---

## 二、路由器怎么转

| 公网（浏览器） | 转到本机 |
|----------------|----------|
| TCP 80 | `LISTEN_HTTP_PORT`（默认 80） |
| TCP 443 | `LISTEN_HTTPS_PORT`（默认 443） |

面板把 `30090` 映射到容器 80 时：

```bash
PUBLIC_HTTP_PORT=30090
LISTEN_HTTP_PORT=80
SSL_MODE=skip          # Let's Encrypt 只会打公网 :80，打不到 30090
```

本机 80 被占用、路由器 80→8080：

```bash
LISTEN_HTTP_PORT=8080
PUBLIC_HTTP_PORT=80
```

---

## 三、环境变量

写在 `ISPmanager/deploy/.env`：

| 变量 | 含义 |
|------|------|
| `BEHIND_NAT` | `auto` / `1` / `0`。有内网网卡时自动为 1 |
| `PUBLIC_IP` | 出口公网 IP。探测失败或多线出口不一致时手填 |
| `SITE_DOMAIN` | 公网域名。不要填内网 IP（IP 只能自签） |
| `SITE_ALIASES` | 裸域等额外名字，空格分隔 |
| `LISTEN_HTTP_PORT` / `LISTEN_HTTPS_PORT` | nginx 本机监听 |
| `PUBLIC_HTTP_PORT` / `PUBLIC_HTTPS_PORT` | 浏览器端口，写入 CSRF / 打印 URL |
| `PUBLIC_PORT` | 主对外端口（HTTPS 启用后等于 `PUBLIC_HTTPS_PORT`） |
| `SSL_MODE` | `auto` / `letsencrypt` / `selfsigned` / `skip` |
| `SSL_HTTP_REDIRECT` | `auto`：公网证书才跳 HTTPS；NAT 自签默认不跳，避免 443 未转发时公网打不开 |

`setup-ssl.sh` **不再**把 `PUBLIC_PORT` 强行改成 443。

---

## 四、SSL 脚本行为

`sudo bash setup-ssl.sh`（或 `manage-service.sh ssl`）：

1. `auto`：用**公网 A 记录**对照出口 IP。一致则申请 Let's Encrypt，否则自签。
2. 申请前检查本机 `http://127.0.0.1:LISTEN_HTTP/.well-known/acme-challenge/`。
3. **不从本机 curl 公网 IP** 作为成败依据（无 NAT 回环时必失败，属正常）。
4. 自签 SAN 含域名、内网 IP、出口 IP，可用 `https://192.168.x.x/` 访问（浏览器告警）。
5. nginx `default_server`：用 IP 当 Host 也能进站。
6. Let's Encrypt 失败时打印转发/DNS 清单，`auto` 回退自签，不把站点跳死。

换公网证书：

```bash
sudo SSL_MODE=letsencrypt bash /opt/app/ISPmanager/deploy/scripts/setup-ssl.sh
```

---

## 五、诊断

```bash
sudo bash /opt/app/ISPmanager/deploy/scripts/check-nat-publish.sh
# 或
sudo bash /opt/app/ISPmanager/deploy/scripts/manage-service.sh nat
```

会打印：内网/出口 IP、公网 A、getent（hosts 干扰）、应转发的端口、本机 ACME 是否通、当前证书类型。

Admin CSRF 仍 403（映射了非 80/443）时：

```bash
# deploy/.env
PUBLIC_HTTPS_PORT=8443
CSRF_PUBLIC_PORTS=8443
sudo bash /opt/app/ISPmanager/deploy/scripts/fix-admin-static-csrf.sh
```

---

## 六、新装示例

公网 VPS（不是 NAT）：

```bash
sudo SITE_DOMAIN=www.example.net CERTBOT_EMAIL=ops@example.net bash install.sh
```

内网机器 + 路由器转发 80/443：

```bash
sudo SITE_DOMAIN=www.example.net \
     BEHIND_NAT=1 \
     PUBLIC_IP=203.0.113.10 \
     CERTBOT_EMAIL=ops@example.net \
     bash install.sh
```

仅内网、没有公网 80：

```bash
sudo SITE_DOMAIN=www.example.net SSL_MODE=selfsigned bash install.sh
# 访问 http://192.168.1.10/  或  https://192.168.1.10/ （自签）
```

外层反代已经做 HTTPS（本机只提供 HTTP）：

```bash
sudo SITE_DOMAIN=www.example.net SSL_MODE=skip PUBLIC_PORT=443 bash install.sh
```

Django 已默认认 `X-Forwarded-Proto`。

---

## 七、已装 2.0.0 如何补上

上传 **2.0.1 update 包**（网页或 CLI），或只更新脚本后：

```bash
sudo bash /opt/app/ISPmanager/deploy/scripts/setup-ssl.sh
sudo bash /opt/app/ISPmanager/deploy/scripts/check-nat-publish.sh
```
