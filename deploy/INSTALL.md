# ISPmanager 安装部署包

目标系统：**全新安装的 Ubuntu 24.04 LTS**（22.04 同样支持），nginx 终止 HTTPS。  
版本：**2.0.5.13**。本包含后端源码、管理端 `dist`、部署脚本。客户门户不在本包内，使用 ISPweb 1.0.10 装到另一台服务器。**目标机无需安装 Node。**  
安装完成后会对齐网页在线升级权限（程序目录属主 + sudoers），后续版本可在管理端「系统维护」上传更新包自动覆盖。

配置只写在 `ISPmanager/deploy/.env` 与 `/etc/ispmanager.env`。完整说明见 `ISPmanager/ReadMe/53-全新安装部署-Ubuntu24.04.md`。

> 本包用于**全新安装**。已有生产环境请使用 `ispmanager-update-*.tar.gz` 与 `update.sh`，避免走完整安装流程。

## 一键安装（管理端与门户分机）

必须解压到 `/opt/app`，不要在 `/root` 下直接安装（程序用户和 nginx 进不了 `/root`）。建议在 `tmux` 里执行，SSH 断开不会中断安装。

```bash
sudo mkdir -p /opt/app
sudo tar -xzf ispmanager-install-*.tar.gz -C /opt/app --strip-components=1
cd /opt/app

sudo DEPLOY_LAYOUT=split \
     SITE_DOMAIN=manager.example.com \
     PORTAL_DOMAIN=portal.example.com \
     PORTAL_API_ALLOW_CIDRS=203.0.113.10/32 \
     CERTBOT_EMAIL=ops@example.com \
     bash install.sh
```

脚本会依次安装 Python 3.12、MySQL Server、nginx、certbot，建库建账号，部署 gunicorn + uvicorn 服务，申请证书并只开放 HTTPS。

`PORTAL_API_SECRET` 留空时由安装脚本生成，写入 `/root/ispmanager-credentials.txt`。把同一串密钥交给 ISPweb 的 `--portal-secret`。门户 API 默认监听 `8443`，只放行 `PORTAL_API_ALLOW_CIDRS`。

门户服务器安装 ISPweb 1.0.10，`--api` 使用凭据文件里的 `portal_api_upstream`，例如 `https://manager.example.com:8443`。

| `SSL_MODE` | 行为 |
|------------|------|
| `auto`（默认） | 域名 A 记录已指向本机则 Let's Encrypt，否则自签 |
| `letsencrypt` | 强制公网证书（80 必须对公网开放） |
| `selfsigned` | 自签，立即 HTTPS（浏览器会告警），证书同时包含本机内网 IP 与公网 IP |
| `skip` | 仅 HTTP，不建议生产使用 |

- 默认 `SSL_HTTP_REDIRECT=1`：HTTP 一律 301 跳到 HTTPS，只允许 HTTPS 访问。80 端口只用于跳转和证书校验。
- 用服务器公网 IP 访问：`https://公网IP/manager`、`https://公网IP/admin/` 可直接使用。公网 IP 已自动写入 `ALLOWED_HOSTS`、CSRF 与 CORS 白名单。Let's Encrypt 证书不含 IP，浏览器会提示证书与地址不符。
- DNS 未就绪时自动使用自签证书。之后执行：

```bash
sudo SSL_MODE=letsencrypt bash /opt/app/ISPmanager/deploy/scripts/setup-ssl.sh
```

首次超级管理员与数据库密码写入：`/root/ispmanager-credentials.txt`（权限 600）。

## 访问

- 管理端：`https://管理端域名/manager`
- Django Admin：`https://管理端域名/admin/`
- 管理 API：`https://管理端域名/api/`，接口文档 `https://管理端域名/api/docs/`
- 门户 API：`https://管理端域名:8443/`（只允许 `PORTAL_API_ALLOW_CIDRS`）
- 客户门户：`https://门户域名/`（在 ISPweb 那台服务器上）

管理端页面请求本机 `/api`。门户页面不直连管理端，由 ISPweb 网关转发到门户 API。其他前端要跨域调用 `/api/` 时，把来源加进 `deploy/.env` 的 `DJANGO_CORS_ALLOWED_ORIGINS` 后执行 `sudo bash /opt/app/ISPmanager/deploy/scripts/deploy.sh`。

## 分步安装

```bash
cd /opt/app/ISPmanager/deploy
cp .env.example .env
cd scripts
sudo bash 01-init-ubuntu.sh
sudo bash 02-setup-database.sh
bash build.sh
sudo bash deploy.sh
sudo bash setup-ssl.sh
sudo bash verify-install.sh
```

详见 `ISPmanager/deploy/README.md`。

## 已有环境请改用更新包

不要对本机已在跑的系统再跑本安装包的「覆盖解压 + install.sh」，除非你明确知道自己在做升级探测。  
推荐使用独立的全量更新包：

```bash
sudo mkdir -p /tmp/isp-update
sudo tar -xzf ispmanager-update-*.tar.gz -C /tmp/isp-update --strip-components=1
sudo bash /tmp/isp-update/update.sh --root /opt/app
```

更新包只覆盖程序，不改写客户、订单、账单、上传文件与密钥。
