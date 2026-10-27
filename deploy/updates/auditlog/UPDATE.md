# ISPmanager 更新包 — 全站统一操作日志

**版本**：auditlog / 2026-08-25  
**适用**：已安装的 ISPmanager（管理端 + 后端）  
**说明**：增量覆盖本次日志中心相关文件，并迁移 / 回填 / 重启。不改 nginx、数据库密码与 `.env`。

完整变更见仓库 `ReadMe/37-变更记录-统一操作日志.md`。

---

## 已有环境一键应用

```bash
sudo mkdir -p /tmp/isp-update
sudo tar -xzf ispmanager-update-auditlog-*.tar.gz -C /tmp/isp-update --strip-components=1
sudo bash /tmp/isp-update/apply.sh /opt/app/ISPmanager
```

`apply.sh` 会：

1. 覆盖后端 `log_manager`、认证视图、权限目录与相关配置  
2. 覆盖管理端源码与预构建 `frontend/dist`（`base=/manager/`）  
3. 执行 `migrate log_manager`  
4. 执行 `sync_audit_logs`（回填历史日志 + 同步「操作日志」页权限）  
5. 重启 `ispmanager.service`（若已安装 systemd）

完成后重新登录管理端，打开 **系统设置 → 操作日志**。

---

## 源码树手工步骤（不用 apply.sh 时）

```bash
cd /opt/app/ISPmanager/backend
../venv/bin/python manage.py migrate log_manager --noinput
../venv/bin/python manage.py sync_audit_logs
sudo bash /opt/app/ISPmanager/deploy/scripts/manage-service.sh update
```

新装请用完整发布包 `ispmanager-release-*.tar.gz` + `install.sh`。
