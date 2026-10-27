# 变更记录 — .env source 语法错误与全新系统安装

**日期**：2026-10-04  
**版本**：`2.0.3`（含 2.0.2）

---

## 2.0.3：`ADMIN_FRONTEND_DIR: unbound variable`

全新机执行：

```bash
sudo SITE_DOMAIN=www.10g.hk CERTBOT_EMAIL=admin@10g.hk bash install.sh
```

在「1/8 写入配置模板」失败：

```
/opt/app/ISPmanager/deploy/scripts/common.sh: line 478: ADMIN_FRONTEND_DIR: unbound variable
```

原因：`set -u` 下，`both_frontends_prebuilt` 在 `load_env` 之前读取 `ADMIN_FRONTEND_DIR`。  
修复：`common.sh` 在探测到仓库根后立即给出路径默认值；检查函数对未设置变量做兜底。  
步骤 1 **不**提前 `load_env`，以免冲掉命令行里的 `SITE_DOMAIN`。

已写出的 `.env` 可保留。覆盖解压 **2.0.3 安装包** 后重新执行同一条命令即可。

| 文件 | SHA256 |
|------|--------|
| `ispmanager-install-2.0.3-20261004-150625.tar.gz` | `e48fccb85dd70425f4f7e038bb7bfd9861b6ab3e817eabe5939f7a1f425a6862` |
| `ispmanager-update-2.0.3-20261004-150625.tar.gz` | `58eb1f33c15b08bdd12c81d49aade21e08412375a589bc06237353648378cccb` |

---

## 2.0.2：`.env` source 语法错误

`.env.example` 中：

```
DJANGO_DEFAULT_FROM_EMAIL=ISP Manager <noreply@example.com>
```

被 `source` 时，bash 把 `<noreply@example.com>` 当成输入重定向。

修复：

1. 模板改为  
   `DJANGO_DEFAULT_FROM_EMAIL="ISP Manager <noreply@example.com>"`
2. 所有部署脚本改用 `source_env_file`：按行拆 `KEY=VALUE`，不再 `source` 整文件
3. `upsert_env_file` / `/etc/ispmanager.env` 写入时用 `shlex.quote`
4. 安装顺序改为：**先写模板 → 完整安装系统包 → 再生成密钥与域名**
5. `01-init-ubuntu.sh` 补齐 `python3`、编译依赖、`mysql-client`

若 `.env` 仍是 2.0.1 半截/未加引号的版本，先删再装：

```bash
sudo rm -f /opt/app/ISPmanager/deploy/.env
cd /opt/app
sudo SITE_DOMAIN=www.10g.hk CERTBOT_EMAIL=admin@10g.hk bash install.sh
```
