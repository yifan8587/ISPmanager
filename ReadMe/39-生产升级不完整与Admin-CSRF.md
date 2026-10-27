# 生产打包升级不完整与 Django Admin CSRF

**日期**：2026-08-25  
**范围**：发布包覆盖已装系统、在线升级、生产环境 `/admin/` 登录

---

## 问题 1：已装系统打包部署后，数据库和程序更新不完整

### 现象

把新发布包解压到已安装目录（或在系统维护里上传更新包）后，常见结果是：

- 前端或后端只更新了一部分（门户 `ISPweb` 未覆盖，或仍用旧 dist）
- 新迁移没全部执行，后台打开报缺字段 / 缺表
- 新 Python 依赖没装上，服务重启后导入失败
- 升级日志显示成功，但 `django_migrations` 与代码不一致
- 权限码、操作日志未同步

### 根因

| 编号 | 位置 | 错误 |
|------|------|------|
| U1 | `system_manager/maintenance.py` `_package_payload_root` | 官方发布包同时含 `ISPmanager/` 与 `ISPweb/` 时，只识别管理端目录，**门户程序不会覆盖** |
| U2 | `maintenance.inspect_upgrade_package` | 会读到 `deploy/VERSION`（`release=ispmanager-release-2026…`），把日期解析成版本号，校验/写入版本错误 |
| U3 | `maintenance.perform_upgrade` | 存在 `apply.sh` 时**不覆盖文件**；无脚本时迁移失败只写日志仍标成功；**不执行 pip / collectstatic** |
| U4 | `deploy/scripts/deploy.sh` | `migrate --noinput` 先跑系统检查。新模型字段尚未落库时检查失败，整次部署中断，库停在旧结构 |
| U5 | `deploy/scripts/manage-service.sh update` | 迁移失败只 `warn` 后继续重启，造成「程序新、库旧」 |
| U6 | `deploy/scripts/build.sh` | `pip install -r requirements.txt` 不带 `--upgrade`，已装的旧版本依赖不会升高 |
| U7 | `deploy/scripts/package.sh` | 发布包缺少 `manifest.json` 与升级入口，在线升级只能走不完整的 overlay 路径 |
| U8 | `run_post_upgrade_hooks` | `sync_permissions` / `sync_audit_logs` 失败被吞掉，菜单与权限目录可能停在旧版 |

### 解决方案（已落地）

1. 发布包写入 `manifest.json`，并带 `apply.sh`；升级时**先覆盖 ISPmanager + ISPweb**，再跑完整升级步骤。
2. 版本识别只认 `manifest.json` 或首行语义化 `VERSION`，忽略 `deploy/VERSION` 元数据。
3. 统一升级步骤（`apply-upgrade.sh` + 后端 `apply_code_upgrade`）：
   - `pip install -r requirements.txt --upgrade`
   - `migrate --noinput --skip-checks`（失败则整次升级失败）
   - `collectstatic --noinput`
   - `sync_permissions` / `sync_audit_logs`
4. `deploy.sh` / `manage-service.sh update` 使用同一套迁移策略，**迁移失败即退出**。

已装系统升级建议：

```bash
sudo tar -xzf ispmanager-release-*.tar.gz -C /opt/app --strip-components=1
cd /opt/app
sudo bash install.sh            # 自动判定升级
# 或
sudo bash /opt/app/ISPmanager/deploy/scripts/manage-service.sh update
```

也可在管理端「系统维护」上传同一发布包；覆盖范围包含门户 dist。

---

## 问题 2：生产环境访问 Django Admin，登录后提示 CSRF 错误

### 现象

浏览器打开 `https://站点/admin/`，输入账号密码后出现：

- `CSRF verification failed. Request aborted.`
- 原因类似 `Origin checking failed - https://站点 does not match any trusted origins.`
- 或 `CSRF cookie not set` / `Referer checking failed`

页面样式有时同时丢失（`/static/admin/` 未收集）。

### 根因

Django 4+ / 6 在存在 `Origin` 头时会校验来源。生产由 nginx 终止 HTTPS、反代到本机 gunicorn（HTTP）。

| 编号 | 位置 | 错误 |
|------|------|------|
| C1 | `backend/settings.py` | `DJANGO_SECURE_PROXY_SSL_HEADER` 在 `.env` 里常为 `False`。Django 认为请求是 HTTP，算出的合法 Origin 是 `http://站点`，浏览器实际是 `https://站点`，校验失败 |
| C2 | `CSRF_TRUSTED_ORIGINS` | 只读环境变量；为空且 C1 成立时，HTTPS 登录 POST 必然 403 |
| C3 | `apply_public_origins` | 只根据 `SITE_DOMAIN` 生成来源，**不包含** `SITE_ALIASES`、`ALLOWED_HOSTS` 中的 IP / 别名。用 IP 或别名打开 `/admin/` 会失败 |
| C4 | Cookie / 转发头 | 未设置 `USE_X_FORWARDED_HOST`；`is_secure()` 与 Host 不一致时，登录后的二次 POST 也会 CSRF 失败 |
| C5 | 静态资源 | 升级未跑 `collectstatic` 时 Admin 无样式，表单 token / 静态路径异常，加重登录失败 |

校验逻辑（Django `CsrfViewMiddleware._origin_verified`）：

1. 先比较 `Origin` 与 `{https|http}://{get_host()}`（协议来自 `request.is_secure()`）
2. 再查 `CSRF_TRUSTED_ORIGINS`

因此：**反代 HTTPS 必须让 Django 认出 `is_secure()==True`，并把实际访问主机列入可信来源。**

### 解决方案（已落地）

1. 生产环境默认启用 `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')`。nginx 已传 `X-Forwarded-Proto $scheme`：HTTPS 为 secure，纯 HTTP 仍为 http，不会误判。
2. 启用 `USE_X_FORWARDED_HOST` / `USE_X_FORWARDED_PORT`。
3. 自动把 `SITE_DOMAIN`、`SITE_ALIASES`、`DJANGO_ALLOWED_HOSTS` 展开为 `http(s)://主机[:端口]`，与环境变量中的 `CSRF_TRUSTED_ORIGINS` 合并。
4. 部署脚本 `apply_public_origins` 同步写入别名与 IP。
5. 升级流程强制 `collectstatic`，保证 `/static/admin/` 可用。

登录仍失败时：

```bash
sudo bash /opt/app/ISPmanager/deploy/scripts/fix-admin-static-csrf.sh
```

确认 `/etc/ispmanager.env` 中 `DJANGO_CSRF_TRUSTED_ORIGINS` 含实际访问来源（含端口），然后清 Cookie 再登录。

### 补充：HTTP + 映射端口（如 `:30090`）仍 403

浏览器 Origin 是 `http://www.example.com:30090`，而 nginx `$host` / `X-Forwarded-Host` 只有主机名、`.env` 的 `PUBLIC_PORT` 又是 80/443 时，Django 默认 Origin 精确匹配会失败。

已改为：

1. CSRF 中间件允许「Origin 主机名 = 当前请求主机名」（忽略映射端口与 http/https 差异），攻击站 `evil.com` 仍会被拒绝。
2. nginx 转发使用 `$http_host`，并补上 `X-Forwarded-Port`。
3. `PUBLIC_PORT` / `ADMIN_PUBLIC_PORT` / `CSRF_PUBLIC_PORTS` 都会写入可信来源。

外层映射了非标准端口时，在 `deploy/.env` 写上实际端口后重跑部署或执行上面的修复脚本，并重启后端。

---

## 涉及文件

- `backend/backend/settings.py`
- `backend/backend/csrf_origins.py`
- `backend/system_manager/maintenance.py`
- `deploy/scripts/apply-upgrade.sh`
- `deploy/scripts/package.sh`
- `deploy/scripts/deploy.sh`
- `deploy/scripts/manage-service.sh`
- `deploy/scripts/build.sh`
- `deploy/scripts/common.sh`
- `deploy/scripts/install.sh`
- `deploy/gunicorn/gunicorn_conf.py`
