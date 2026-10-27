# ISPmanager / ISPweb 生产部署说明

面向 **Ubuntu 22.04 LTS**（兼容 24.04）+ **nginx** 的一体化方案。  
**不修改业务仓库文件**；配置只写入 `deploy/.env` 与 `/etc/ispmanager.env`。

当前发布版本 **2.0.5.4**。安装与在线升级见 [`../ReadMe/50-生产发布-2.0.0-安装与在线升级.md`](../ReadMe/50-生产发布-2.0.0-安装与在线升级.md)；内网 NAT / 域名 / SSL 见 [`../ReadMe/51-内网NAT发布与SSL.md`](../ReadMe/51-内网NAT发布与SSL.md)；全新机 `.env` / `ADMIN_FRONTEND_DIR` 见 [`../ReadMe/52-变更记录-env语法与全新系统安装.md`](../ReadMe/52-变更记录-env语法与全新系统安装.md)。分机部署见本文「管理端与门户分机部署」。

`scripts/package.sh` 一次构建打出两套包，目标机**无需 Node**：

- **安装部署包** `ispmanager-install-*.tar.gz`：新机器一键安装
- **全量更新包** `ispmanager-update-*.tar.gz`：已装系统覆盖程序，**不改写业务数据**

---

## 一、架构

```
                         nginx (80/443)   server_name = SITE_DOMAIN
  ┌──────────────────────────────────────────────────────────────────┐
  │  /                      → ISPweb/dist                 客户门户   │
  │  /manager/              → ISPmanager/frontend/dist    管理端 SPA │
  │  /admin/                → gunicorn                    Django Admin│
  │  /api/  /ws/            → gunicorn                    REST / WS  │
  │  /static/  /media/      → collectstatic / 上传                   │
  └───────────────────────────────┬──────────────────────────────────┘
                                  │ 127.0.0.1:8000
                     gunicorn + uvicorn worker（systemd: ispmanager.service）
                                  │
                               MySQL 8
```

生产 URL（同一域名）：

- 客户门户：`https://www.example.com/`
- 管理端：`https://www.example.com/manager`
- Django Admin：`https://www.example.com/admin/`
- API / WebSocket：`https://www.example.com/api/`、`/ws/`（**不要**挂到 `/manager` 下）

- 后端：Django 6 + DRF + Channels，**需要 Python 3.12**。22.04 上由脚本通过 deadsnakes 安装，不改项目代码。
- 两个 SPA 使用相对路径 `/api`、`/ws`，与页面同域，**无跨域**。
- 管理端生产构建 `base=/manager/`，刷新 `/manager/login` 等由 nginx 回退到 `index.html`。
- HTTPS 由 nginx 终止；HTTP 仅保留 ACME 与跳转。证书用 `certbot certonly --webroot` 或自签，**不会**让 certbot 改写站点文件。

---

## 二、安装包与全量更新包

在构建机（本仓库，需 Node）生成压缩包：

```bash
cd ISPmanager/deploy/scripts
bash package.sh
```

产物（均在 `ISPmanager/deploy/`）：

| 文件 | 用途 |
|------|------|
| `ispmanager-install-<版本>-<时间戳>.tar.gz` | 全新安装 |
| `ispmanager-update-<版本>-<时间戳>.tar.gz` | 已装系统全量更新 |
| `ispmanager-install-latest.tar.gz` / `ispmanager-update-latest.tar.gz` | 稳定文件名 |
| `ispmanager-release-latest.tar.gz` | 兼容旧名，内容等于安装包 |

### 2.1 新机器一键安装

```bash
sudo mkdir -p /opt/app
sudo tar -xzf ispmanager-install-*.tar.gz -C /opt/app --strip-components=1
cd /opt/app

sudo SITE_DOMAIN=www.example.net \
     CERTBOT_EMAIL=ops@example.net \
     bash install.sh
```

`install.sh` 依次：生成密钥与 `.env` → 装系统依赖（Python 3.12 / nginx / MySQL / certbot）→ 建库 → 装后端依赖 + collectstatic → systemd + nginx → HTTPS → 自检。

| `SSL_MODE` | 行为 |
|------------|------|
| `auto`（默认） | 站点域名 A 记录已指向本机则 Let's Encrypt，否则自签 |
| `letsencrypt` | 强制公网证书（80 必须对公网开放） |
| `selfsigned` | 自签，立即 HTTPS（浏览器告警） |
| `skip` | 仅 HTTP |

首次账号与数据库密码写在 `/root/ispmanager-credentials.txt`（600）。

启用 HTTPS 后访问：

- 门户 `https://站点域名/`
- 管理端 `https://站点域名/manager`
- Admin `https://站点域名/admin/`
- API `https://站点域名/api/`

DNS 后补时：`sudo SSL_MODE=letsencrypt bash /opt/app/ISPmanager/deploy/scripts/setup-ssl.sh`

### 2.2 已装系统全量更新（不影响业务数据）

把更新包解压到**临时目录**再执行，不要覆盖 `deploy/.env`：

```bash
sudo mkdir -p /tmp/isp-update
sudo tar -xzf ispmanager-update-*.tar.gz -C /tmp/isp-update --strip-components=1
sudo bash /tmp/isp-update/update.sh --root /opt/app
```

或：`sudo bash /opt/app/ISPmanager/deploy/scripts/manage-service.sh update-package --payload /tmp/isp-update --root /opt/app`

也可在管理端「系统维护」上传该更新包（版本须高于当前）。

`update.sh` 只覆盖程序（后端、两端 dist、部署脚本），执行 pip / migrate / collectstatic 后重启。  
**保留** `deploy/.env`、`/etc/ispmanager.env`、MySQL 业务数据、证书与 `media`。迁移失败会中止。  
备份目录：`/var/backups/ispmanager/<时间戳>/`。详见 `ReadMe/40-安装包与全量更新包.md`。

源码树本机更新仍可用：`sudo bash /opt/app/ISPmanager/deploy/scripts/manage-service.sh update`。

仅升级「统一操作日志」时，可用增量包（不必整包重装）：

```bash
sudo tar -xzf ispmanager-update-auditlog-*.tar.gz -C /tmp --strip-components=1
sudo bash /tmp/apply.sh /opt/app/ISPmanager
```

说明见包内 `UPDATE.md` 与 `ReadMe/37-变更记录-统一操作日志.md`。

---

## 三、源码树分步安装（开发机同步到服务器）

目录须同级：

```
/opt/app/ISPmanager
/opt/app/ISPweb
```

```bash
cd /opt/app/ISPmanager/deploy
cp .env.example .env          # 或直接跑 install.sh 自动生成
sudo bash scripts/install.sh
```

等价分步：

```bash
cd scripts
sudo bash 01-init-ubuntu.sh
sudo bash 02-setup-database.sh
bash build.sh
sudo bash deploy.sh
sudo bash setup-ssl.sh
sudo bash verify-install.sh
```

**必改/自动生成**：`SITE_DOMAIN`、`DJANGO_SECRET_KEY`、`DJANGO_ALLOWED_HOSTS`、`DB_PASSWORD`。  
兼容旧变量 `ADMIN_DOMAIN` / `PORTAL_DOMAIN`：脚本会规范为与 `SITE_DOMAIN` 相同。  
在线支付另设独立的 `PAYMENT_CONFIG_ENCRYPTION_KEY`，`PAYMENT_FRONTEND_URL` 须为 `https://站点域名/manager`。

`deploy.sh` 会启用：

- `ispmanager-maintenance.timer`（每 15 分钟：履约 / 预占 / 退款 / 降配）
- `ispmanager-daily-jobs.timer`（01:15：流量出账 / 逾期 / SLA / 品质汇总）
- `ispmanager-instance-expiry.timer`（09:00：催缴；默认不停机/回收）

再次执行 `deploy.sh` **不会拆掉已有证书**，有证书则继续渲染 HTTPS 站点。

---

## 四、目录

```
ISPmanager/deploy/
├── README.md
├── .env.example
├── scripts/
│   ├── install.sh              # 一键安装
│   ├── update-from-package.sh  # 已装系统全量更新
│   ├── apply-upgrade.sh        # pip / migrate / collectstatic
│   ├── verify-install.sh
│   ├── common.sh
│   ├── 01-init-ubuntu.sh       # 22.04/24.04：Python 3.12 / nginx / mysql / certbot
│   ├── 02-setup-database.sh
│   ├── build.sh
│   ├── deploy.sh
│   ├── setup-ssl.sh            # webroot 签发或自签，再渲染 HTTPS 模板
│   ├── package.sh              # 同时打安装包与更新包
│   ├── fix-admin-static-csrf.sh
│   ├── prepare-web-upgrade.sh  # 网页升级属主 + sudoers
│   └── manage-service.sh
├── systemd/                    # gunicorn 与定时任务模板
├── nginx/
│   ├── ispmanager.conf.template
│   ├── ispmanager-ssl.conf.template
│   └── snippets/proxy_params.conf
└── gunicorn/gunicorn_conf.py
```

---

## 五、运维

```bash
cd ISPmanager/deploy/scripts
bash manage-service.sh status
sudo bash manage-service.sh restart
bash manage-service.sh logs
sudo bash manage-service.sh update
sudo bash manage-service.sh update-package --payload /tmp/isp-update --root /opt/app
sudo bash manage-service.sh ssl
bash manage-service.sh verify
```

代码更新：使用全量更新包 `update.sh`，或源码树执行 `sudo bash manage-service.sh update`。  
安装前检查：`bash manage-service.sh preflight`。系统调优：`sudo bash manage-service.sh tune`。

POP 节点按 IP + 自签证书同步失败（`CERTIFICATE_VERIFY_FAILED`）时，先同步含修复的源码，再执行：

```bash
sudo bash /opt/app/ISPmanager/deploy/patch-pop-selfsigned-ssl.sh
```

该补丁会迁移 `PopPoint.api_verify_ssl`（默认关闭校验）并重启后端。新装请用 `ispmanager-release-latest.tar.gz`。

WebSSH/VNC 多 worker 建议 Redis：

```bash
# deploy/.env
CHANNEL_LAYER_BACKEND=redis
CHANNEL_REDIS_URL=redis://127.0.0.1:6379/0
```

`01-init-ubuntu.sh` 在该配置下会安装 `redis-server`。

---

## 六、设计要点

1. **Django Admin**：仍在站点根路径 `/admin/`，`STATIC_URL=/static/`，**不要**设置 `FORCE_SCRIPT_NAME`。无样式或 CSRF 403 时跑 `fix-admin-static-csrf.sh`。
2. **HTTPS 识别**：`DJANGO_SECURE_PROXY_SSL_HEADER=True`，nginx 传 `X-Forwarded-Proto`。
3. **CSRF/CORS**：按 `SITE_DOMAIN`、别名与 `ALLOWED_HOSTS` 生成 `https://域名`（及 http / 非标准端口）。路径不参与 Origin。生产强制识别 `X-Forwarded-Proto`，避免 `/admin/` 登录 CSRF 403。
4. **生产启动校验**：`DJANGO_DEBUG=False` 时拒绝默认 SECRET、`ALLOWED_HOSTS=*`、CORS 全放行。
5. **权限**：nginx 需能遍历到 `dist` / `staticfiles`；脚本会补齐路径执行位。安装后把程序目录交给 `ispmanager`，并写入 `/etc/sudoers.d/ispmanager-web-upgrade`，后续版本可在管理端「系统维护」网页升级并自动重启。
6. **管理端静态资源**：生产 `vite base=/manager/`，与 `ADMIN_BASE_PATH` 必须一致。

---

## 七、排查

| 现象 | 处理 |
|------|------|
| 后端起不来 | `journalctl -u ispmanager -n 50`（密钥 / 库密码 / ALLOWED_HOSTS） |
| Python 版本不对 | 22.04 须有 `python3.12`；删掉旧 `venv` 后重跑 `build.sh` |
| admin 无样式 | `sudo bash fix-admin-static-csrf.sh` |
| 管理端刷新 404 / JS 404 | dist 是否带 `/manager/`；nginx 是否 `^~ /manager/` |
| 门户刷新 404 | nginx `try_files ... /index.html` |
| noVNC 失败 | `/ws/` 是否带 Upgrade；gunicorn 是否 uvicorn worker |
| certbot 失败 | 公网 A 是否指向出口 IP（不要看 /etc/hosts）；路由器是否转发 80→本机；`sudo bash check-nat-publish.sh` |
| 内网能开、公网打不开 | 转发 80/443 到 LISTEN_* 端口；域名 A 填路由器公网 IP |
| 启用 HTTPS 后公网 HTTP 挂了 | NAT 未转发 443。设 `SSL_HTTP_REDIRECT=0` 后重跑 `setup-ssl.sh` |
| 重跑 deploy 丢 HTTPS | 当前脚本会检测已有证书并重新渲染 SSL 模板 |

---

## 八、管理端与门户分机部署

`DEPLOY_LAYOUT=split` 时，本机只跑 ISPmanager。客户门户用 ISPweb 自己的安装包放到另一台服务器。浏览器只打开门户域名，页面上的数据由 ISPweb 网关转发到本机的门户 API 端口。

```
浏览器 --HTTPS--> 门户服务器 Nginx
                    |-- 静态页面
                    |-- /_g/ /_gws/ --> 本机网关（校验混淆和签名）
                                         |
                                         | 只允许门户路径 + 共享密钥
                                         v
                               管理端服务器 :8443（只放行门户服务器 IP）
                                         |
                                         v
                               gunicorn /api/portal、登录、VNC
```

管理端服务器：

```bash
# deploy/.env
DEPLOY_LAYOUT=split
SITE_DOMAIN=manager.example.com
PORTAL_DOMAIN=portal.example.com
PORTAL_API_ALLOW_CIDRS=203.0.113.10/32
PORTAL_API_PORT=8443
```

`PORTAL_API_SECRET` 留空时，`install.sh` 会生成，并写入 `/root/ispmanager-credentials.txt`。把同一串密钥配到门户服务器。

门户服务器见 ISPweb 的安装说明。API 地址写成 `https://manager.example.com:8443`，不要写成管理端的 443，也不要写成公网 IP。8443 使用和管理端相同的证书，所以地址里的主机名必须是 `SITE_DOMAIN`。

这条通道只放行门户会用到的路径：登录、`/api/portal/`、支付交易查询，以及 VNC 的 `/ws/resource/vnc/`。管理接口仍然只在管理端域名上。公网站点上的 `/api/portal/` 会直接断开。

支付完成后再跳回 `https://门户域名/...`。支付平台的异步通知仍回到管理端自己的公网地址。

## 九、端口

| 组件 | 监听 | 说明 |
|------|------|------|
| nginx | 80 / 443 | 对外入口；80 仅 ACME 与跳转 |
| gunicorn | 127.0.0.1:8000 | 仅本机 |
| MySQL | 127.0.0.1:3306 | 仅本机 |
| 门户 API | PORTAL_API_PORT（默认 8443） | 仅 split。只放行门户服务器来源 |

运行时环境：`/etc/ispmanager.env`  
nginx 站点：`/etc/nginx/sites-available/ispmanager.conf`
