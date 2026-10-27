# 变更记录 — 在线升级 Permission denied（ReadMe / 前端属主）

**日期**：2026-09-06  
**版本**：`1.1.7`  
**范围**：管理端「系统维护」在线升级、`deploy` 属主对齐

---

## 问题

上传 `ispmanager-install-1.1.6-*.tar.gz` 或更新包后，升级日志立刻失败：

```
[Errno 13] Permission denied: '/opt/app/ISPmanager/ReadMe/45-变更记录-IP录入网关与NAT交付.md'
```

安装与 CLI 更新以 **root** 落盘。`deploy.sh` 原先只把 `backend/` 改成 `APP_USER`（默认 `ispmanager`）。  
gunicorn 以该用户覆盖文件时，`ReadMe/`、管理端 `frontend/`、门户 `ISPweb/`、部分 `deploy/` 仍是 `root:root` 644，`shutil.copy2` 无法打开目标文件写入。

`1.1.6` 起发布包带上了 `ReadMe`，覆盖会先碰到文档，于是表现为「升级一开始就失败」。即便跳过文档，前端 / 门户同样会踩同一类错误。`copy2` 还会把包内只读位拷到目标机，下次升级即使属主正确也可能再被拒绝。

---

## 修复

1. **覆盖改为可写替换**：先去掉目标只读位或删后写入，落盘权限为 `644` / `755`，不再复制源文件只读位。
2. **文档不阻断升级**：`ReadMe/*.md` 若仍无权写入则跳过并记日志；后端 / 前端 / 门户失败则汇总报错，并给出修复命令。
3. **安装与 CLI 升级后对齐属主**：`ensure_overlay_ownership` 把 backend、frontend、ISPweb、ReadMe、deploy 交给 `APP_USER`；`deploy/.env` 保持 `root:APP_GROUP` `640`。
4. **一次性修复脚本**：`deploy/scripts/fix-overlay-permissions.sh`
5. 管理端上传文件名含 `install` 时提示改用 `ispmanager-update-*.tar.gz`。

---

## 已失败环境怎么继续

当前进程仍是旧代码，**必须先让服务用户能写程序目录**，再重新升级。

```bash
sudo chown -R ispmanager:ispmanager \
  /opt/app/ISPmanager/backend \
  /opt/app/ISPmanager/frontend \
  /opt/app/ISPmanager/ReadMe \
  /opt/app/ISPmanager/deploy \
  /opt/app/ISPweb
sudo chown root:ispmanager /opt/app/ISPmanager/deploy/.env
sudo chmod 640 /opt/app/ISPmanager/deploy/.env
```

若已拿到含本修复的更新包，更稳妥的是用 **root CLI**（不依赖当前 Web 覆盖逻辑）：

```bash
sudo mkdir -p /tmp/isp-update
sudo tar -xzf ispmanager-update-1.1.7-*.tar.gz -C /tmp/isp-update --strip-components=1
sudo bash /tmp/isp-update/update.sh --root /opt/app
```

或在更新包落地后：

```bash
sudo bash /opt/app/ISPmanager/deploy/scripts/fix-overlay-permissions.sh --root /opt/app
```

然后回到「系统维护」用 **update** 包重新升级（不要用 `ispmanager-install-*.tar.gz`）。
