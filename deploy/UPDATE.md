# ISPmanager 全量更新包

本包用于**已安装系统**的全量程序更新（目标版本 **2.0.5.4**）。  
**不会改写业务数据**：客户、订单、账单、实例、上传文件（`media`）、站点密钥与数据库账号密码均保留。  
更新后会再次对齐网页升级权限，保证下一个版本仍可在管理端「系统维护」上传覆盖。

2.0.5.4 的更新包只含管理端，不含 ISPweb。目标机 `deploy/.env` 里的 `DEPLOY_LAYOUT` 须为 `split`。门户升级请使用 ISPweb 1.0.2 的安装包和 `deploy/update.sh`。

目标机 **无需 Node**。更新步骤：备份配置（可选 mysqldump）→ 覆盖程序 → `pip` / `migrate` / `collectstatic` → 重启服务。

> 本包**不能**用于全新安装。新服务器请使用 `ispmanager-install-*.tar.gz` 与 `install.sh`。

## 推荐用法（解压到临时目录）

```bash
sudo mkdir -p /tmp/isp-update
sudo tar -xzf ispmanager-update-*.tar.gz -C /tmp/isp-update --strip-components=1
sudo bash /tmp/isp-update/update.sh --root /opt/app
```

安装路径不是 `/opt/app` 时加上 `--root`。也可：

```bash
sudo bash /opt/app/ISPmanager/deploy/scripts/manage-service.sh update-package \
    --payload /tmp/isp-update --root /opt/app
```

跳过数据库备份：`sudo SKIP_DB_BACKUP=1 bash /tmp/isp-update/update.sh --root /opt/app`

## 会改 / 不会改

| 会更新 | 明确不改 |
|--------|----------|
| 后端 Python 代码与依赖 | MySQL 里的业务行数据（只做 schema 迁移） |
| 管理端 `frontend/dist` | `ISPmanager/deploy/.env` |
| 门户 `ISPweb/dist` | `/etc/ispmanager.env` 中的密钥与库密码 |
| 部署脚本、nginx 模板 | `backend/media` 上传文件 |
| 权限码 / 操作日志目录同步 | 证书、超级管理员密码、客户账号 |

配置与库备份目录：`/var/backups/ispmanager/<时间戳>/`。  
迁移失败会**中止**，避免程序新、库旧。

## 管理端「系统维护」上传

本包含 `ISPmanager/VERSION`、`manifest.json`、`apply.sh`，可直接在管理端上传。  
维护模块会同时覆盖 ISPmanager 与 ISPweb，再执行依赖 / 迁移 / 静态收集。  
版本号须**高于**当前系统版本。

## 不要做的事

- 不要用本包跑 `install.sh --fresh` 或 `FORCE_FRESH=1`
- 不要把更新包解压后覆盖 `deploy/.env`
- 不要还原备份里的 `database.sql.gz`，除非你在做灾难恢复（那会回退业务数据）
