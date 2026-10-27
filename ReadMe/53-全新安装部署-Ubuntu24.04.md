# 全新安装部署 — Ubuntu 24.04（管理端 + 客户门户分机）

**版本**：ISPmanager `2.0.5.13`，ISPweb `1.0.10`  
**适用**：两台刚装好系统的 Ubuntu 24.04 LTS（22.04 同样可用），一台装管理端 ISPmanager，一台装客户门户 ISPweb。

---

## 1. 部署结构

```text
客户浏览器 ──HTTPS 443──▶ 门户服务器（ISPweb）
                           nginx ─ 静态页面
                                 └ /_g/ /_gws/ ─▶ 本机网关（127.0.0.1:8765，校验签名）
                                                     │ HTTPS + 门户密钥
                                                     ▼
管理员浏览器 ──HTTPS 443──▶ 管理端服务器（ISPmanager）门户 API 端口 8443（只放行门户服务器 IP）
                           nginx ─ /manager    管理端页面
                                 ├ /admin/     Django Admin
                                 ├ /api/       管理 API（含 /api/docs/）
                                 ├ /ws/        WebSSH / noVNC
                                 └ 反代 ─▶ gunicorn + uvicorn（127.0.0.1:8000）─▶ MySQL 8
```

| 端口 | 服务器 | 用途 | 对谁开放 |
|---|---|---|---|
| 80 | 两台 | 只做 301 跳 HTTPS 和 Let's Encrypt 校验 | 公网 |
| 443 | 两台 | 页面、API、WebSocket | 公网 |
| 8443 | 管理端 | 门户 API | 只放行门户服务器出口 IP |
| 3306 / 8000 / 8765 | 本机 | MySQL、应用、门户网关 | 只监听 127.0.0.1 |

云服务器的安全组、机房防火墙或 NAT 路由器需要按上表放行或转发同样的端口。

## 2. 准备

- 每台至少 2 核 2 GB 内存、20 GB 磁盘。MySQL 8 在 1 GB 内存下容易安装失败。
- 两个域名，例如 `manager.example.com`、`portal.example.com`，A 记录分别指向两台服务器的公网 IP。DNS 没生效时脚本先用自签证书，之后可一条命令换成正式证书。
- 记下门户服务器的**出口公网 IP**，管理端安装时要填。
- 以 root 或有 sudo 的账号登录。机器能访问 Ubuntu 软件源和 PyPI。

## 3. 安装管理端（ISPmanager）

上传 `ispmanager-install-2.0.5.13-*.tar.gz` 与 `.sha256` 到管理端服务器。

> 必须解压到 `/opt/app`，不要在 `/root` 下直接安装：`/root` 只有 root 能进入，程序用户 `ispmanager` 与 nginx 都读不到，服务会起不来、页面 403。安装前检查发现这种情况会直接停止。  
> 安装要跑十几分钟，建议在 `tmux` 里执行（`sudo apt-get install -y tmux && tmux new -s isp`），SSH 断开后用 `tmux attach -t isp` 回来，安装不会被中断。

```bash
sha256sum -c ispmanager-install-2.0.5.13-*.tar.gz.sha256
sudo mkdir -p /opt/app
sudo tar -xzf ispmanager-install-2.0.5.13-*.tar.gz -C /opt/app --strip-components=1
cd /opt/app

sudo DEPLOY_LAYOUT=split \
     SITE_DOMAIN=manager.example.com \
     PORTAL_DOMAIN=portal.example.com \
     PORTAL_API_ALLOW_CIDRS=门户服务器出口IP/32 \
     CERTBOT_EMAIL=ops@example.com \
     bash install.sh 2>&1 | tee /root/ispmanager-install.log
```

脚本自动完成：

1. 安装前检查（系统版本、内存、磁盘、是否已有安装）。
2. 安装系统组件：`python3.12`、`python3.12-venv`、编译依赖、`mysql-server`、`mysql-client`、`nginx`、`certbot`、`python3-certbot-nginx`、`ufw` 规则。发布包自带管理端 `dist`，不装 Node.js。
3. 生成 `DJANGO_SECRET_KEY`、支付配置加密密钥、数据库密码、门户 API 密钥和超级管理员密码，写入 `deploy/.env` 与 `/etc/ispmanager.env`。
4. 内核与 MySQL 参数调优。
5. 建库 `ISPmanager` 与账号 `ispmanager`（只允许本机连接）。
6. 建 Python 虚拟环境、安装依赖、`collectstatic`（含 Django Admin 静态文件）、数据库迁移、创建超级管理员。
7. 安装 systemd 服务：`ispmanager`（gunicorn + uvicorn，支持 WebSocket）及定时任务服务；渲染 nginx 站点与门户 API 端口。
8. 申请证书并切换 HTTPS，写入证书自动续期。
9. 自检，打印访问地址。

安装结束后查看凭据：

```bash
sudo cat /root/ispmanager-credentials.txt
```

需要交给门户的两项：`portal_api_upstream`（例如 `https://manager.example.com:8443`）和 `portal_api_secret`。

### 只允许 HTTPS

`SSL_HTTP_REDIRECT` 默认为 `1`：任何 HTTP 请求（域名或 IP）都返回 301 跳到同一地址的 HTTPS，80 端口只保留 Let's Encrypt 校验路径。门户 API 端口 8443 只有 HTTPS。

证书方式由 `SSL_MODE` 决定：

| `SSL_MODE` | 行为 |
|---|---|
| `auto`（默认） | 域名 A 记录已指向本机出口 IP 时申请 Let's Encrypt，否则自签 |
| `letsencrypt` | 强制 Let's Encrypt，80 必须能从公网访问 |
| `selfsigned` | 自签证书，包含域名、本机内网 IP 和公网 IP |

DNS 生效后换正式证书：

```bash
sudo SSL_MODE=letsencrypt bash /opt/app/ISPmanager/deploy/scripts/setup-ssl.sh
```

### 用公网 IP 访问

安装时自动探测本机公网 IP（NAT 后探测出口 IP），写入 `PUBLIC_IP`、`DJANGO_ALLOWED_HOSTS`、CSRF 与 CORS 白名单，nginx 的 HTTPS 站点是默认站点。因此下面的地址都能直接使用：

- `https://公网IP/manager`
- `https://公网IP/admin/`
- `https://公网IP/api/`

Let's Encrypt 证书只包含域名，用 IP 访问时浏览器会提示证书与地址不符，确认后即可继续。探测不准时安装前加 `PUBLIC_IP=实际公网IP`。

NAT 后的服务器：路由器把公网 80、443、8443 转发到本机同样端口。浏览器端口与本机端口不同时，设置 `PUBLIC_HTTPS_PORT`、`LISTEN_HTTPS_PORT`，详见 [51-内网NAT发布与SSL.md](./51-内网NAT发布与SSL.md)。

### 前端 API 与跨域

| 入口 | 地址 | 鉴权 |
|---|---|---|
| 管理 API | `https://管理端域名/api/` | JWT（`/api/auth/token/`） |
| 门户 API | `https://管理端域名:8443/api/portal/` | 只接受门户网关：来源 IP 白名单 + `X-ISP-Portal-Key` |
| WebSocket | `wss://管理端域名/ws/...` | JWT |
| 接口文档 | `https://管理端域名/api/docs/`、`/api/redoc/`、`/api/schema/` | — |

CORS 白名单按域名、别名、公网 IP、映射端口自动生成。另有前端要从浏览器直接跨域调用管理 API 时：

```bash
sudo nano /opt/app/ISPmanager/deploy/.env
# DJANGO_CORS_ALLOWED_ORIGINS=https://app.example.net,https://app.example.net:8443
# 需要表单 POST 时同样加到 DJANGO_CSRF_TRUSTED_ORIGINS
sudo bash /opt/app/ISPmanager/deploy/scripts/deploy.sh
```

脚本只追加自动生成的来源，不会删掉手工写的值。来源必须写完整协议与端口，不能写 `*`。

### Django Admin

- 地址：`https://管理端域名/admin/`
- 账号：凭据文件里的 `django_superuser` / `django_superuser_password`
- 静态文件由 nginx 直接提供（`DJANGO_STATIC_ROOT`），登录表单的 CSRF 来源已包含域名、公网 IP 与映射端口。

## 4. 安装客户门户（ISPweb）

上传 `ispweb-1.0.10-*.tar.gz` 与 `.sha256` 到门户服务器：

```bash
sha256sum -c ispweb-1.0.10-*.tar.gz.sha256
tar -xzf ispweb-1.0.10-*.tar.gz
cd ispweb-1.0.10-*

sudo ./deploy/install.sh \
  --domain portal.example.com \
  --api https://manager.example.com:8443 \
  --portal-secret '管理端凭据文件里的 portal_api_secret' \
  --ssl certbot --email ops@example.com \
  --allow-ip
```

- `--api` 就是门户对接的后端 API，写管理端凭据里的 `portal_api_upstream`。管理端用自签证书时，脚本会自动钉住它的证书。
- `--allow-ip` 允许用门户服务器公网 IP 访问；不需要就去掉。NAT 后探测不准时用 `--public-ip 实际IP`。
- DNS 未就绪时先用 `--ssl self-signed`，之后再用 `--ssl certbot` 重新执行（加 `--fresh`）。

脚本会安装 `nginx`、`python3`、`openssl`、`certbot`，部署前端到 `/opt/ispweb/current`，启动 `ispweb-urlguard` 网关，HTTP 一律跳 HTTPS。详见 ISPweb 项目 `ReadMe/安装部署说明.md`。

## 5. 安装后检查

管理端：

```bash
sudo bash /opt/app/ISPmanager/deploy/scripts/verify-install.sh
systemctl status ispmanager nginx mysql --no-pager
curl -I http://manager.example.com/          # 301 到 https
curl -kI https://manager.example.com/admin/  # 302 到登录页
curl -kI https://公网IP/manager/             # 200
```

门户：

```bash
systemctl status nginx ispweb-urlguard --no-pager
curl -fsS http://127.0.0.1:8765/healthz
curl -I http://portal.example.com/           # 301 到 https
curl -I https://portal.example.com/          # 200
```

从门户服务器确认能连到管理端门户 API（返回任意 HTTP 状态即说明网络已通，超时说明端口没放行）：

```bash
curl -k -o /dev/null -w '%{http_code}\n' https://manager.example.com:8443/api/portal/
```

最后用浏览器打开门户注册登录、下单，再到管理端处理，确认整条链路可用。

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| 安装中途 SSH 断开 | 重新登录后进入 `tmux attach -t isp`；没用 tmux 时直接在 `/opt/app` 重新执行同一条安装命令，已有数据库和密钥会被识别并保留 |
| 在 `/root` 下解压安装过 | 按上面的步骤解压到 `/opt/app` 重新执行安装命令，systemd 与 nginx 会改指向 `/opt/app`，数据库和密钥沿用 |
| MySQL 安装失败 | 多为内存不足。加 2 GB swap 后重新执行 `install.sh` |
| 证书申请失败，自动用了自签 | 检查域名 A 记录与 80 端口，然后执行上面的 `SSL_MODE=letsencrypt` 命令 |
| 门户页面打开但接口全部失败 | 管理端 `PORTAL_API_ALLOW_CIDRS` 没包含门户出口 IP，或安全组没放行 8443，或两边密钥不一致（对比安装输出的密钥指纹） |
| Django Admin 登录 403 CSRF | 访问地址不在白名单，例如新加了域名或映射端口。加到 `SITE_ALIASES` 或 `CSRF_PUBLIC_PORTS` 后执行 `deploy.sh` |
| 手动执行 `manage.py` 报数据库 `Access denied for user 'root'` | 没加载运行时环境。用 `sudo systemd-run --pipe --wait --quiet -p EnvironmentFile=/etc/ispmanager.env -p User=ispmanager -p WorkingDirectory=/opt/app/ISPmanager/backend /opt/app/ISPmanager/venv/bin/python manage.py <命令>` |
| 客户 noVNC 连不上 | 在管理端用上一行的方式执行 `manage.py vnc_diagnose <VPS编号>`，按输出定位节点或 PVE |

## 7. 后续更新

- 管理端：在「系统维护」上传 `ispmanager-update-*.tar.gz`，或命令行执行更新包里的 `update.sh --root /opt/app`。
- 门户：`sudo ./deploy/update.sh ispweb-新版本.tar.gz`。域名、证书、API 上游和 IP 访问设置保持不变。
