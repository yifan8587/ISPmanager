# 变更记录 — Cloud 镜像部署替代 vzdump / vma.zst

**日期**：2026-09-06  

**范围**：vpopmanager `pvemanager`、ISPmanager 系统模版目录与 VPS 自动开通  
**对应**：所有 QEMU 虚机改为发行版 Cloud 版 `.qcow2` / `.img` 部署；模板库提供可覆盖更新的下载窗口；开通时写入 IP / 密码

---

## 问题

原先 QEMU 开通走 `vzdump` `.vma.zst` 恢复。该方式体积大、制作成本高，也不符合各发行版官方 Cloud 镜像 + cloud-init 的标准流程。中心系统模版目录同样按备份文件名下发，节点下载窗口默认落到 `/var/lib/vz/dump/`。

---

## 行为

1. **部署方式**  
   空壳 VM（`virtio-scsi-single` / `ostype=l26`）→ `scsi0` `import-from` Cloud 镜像 → 扩盘 → `boot=order=scsi0`、串口控制台、qemu-guest-agent → 挂 `ide2` cloud-init → 写入用户 / 密码 / IP / DNS。  
   `.vma.zst` / vzdump 开通直接拒绝。已转换为 PVE 模板虚机的克隆、以及 LXC `vztmpl` 仍可用。
2. **镜像存放**  
   Cloud 镜像按 PVE `iso` content 落到 `local`：`/var/lib/vz/template/iso/`。虚机磁盘仍写 `local-lvm` 等 images 存储。
3. **下载 / 更新窗口**  
   节点模板库支持选择 Ubuntu / Debian / Alma / Rocky 官方 Cloud URL，下载或覆盖更新后自动注册为 `cloud` 类型。
4. **中心目录与开通**  
   系统模版默认类型为 Cloud 镜像；可从指定 POP 的 `/api/pvemanager/vps/os-catalog/` 同步，或导入官方 Cloud 目录。已配好的 Cloud URL 不覆盖；同名的 `.vma.zst` / PVE 备份会就地升级。产品管理绑定系统版本，门户只展示可 Cloud 开通的条目。POP 缺失时按 URL 下载到 iso 存储。自动开通与重装会生成/沿用密码，并把空闲或已分配 IP 的地址 / CIDR / 网关写入 cloud-init；门户改密下发到节点 Guest Agent。

---

## 部署

1. 节点执行迁移 `pvemanager.0006_pvetemplate_cloud_source`。  
2. 中心执行迁移 `system_manager.0025_os_template_cloud_source`。  
3. 在「系统模版」点「导入官方 Cloud 目录」或「从 POP 同步」，把原 `.vma.zst` 条目升级为发行版 Cloud 镜像。  
4. 产品管理里为 VPS 勾选可用系统版本（不选则门户展示全部可部署 Cloud 版本）。  
5. 在节点模板库下载或扫描 Cloud 镜像后即可开通；节点没有该文件时按中心 URL 自动下载。

生产安装包 / 更新包与操作步骤见 [47-生产发布-1.1.6-安装与在线升级.md](./47-生产发布-1.1.6-安装与在线升级.md)。节点须同步升级 vpopmanager 1.1.0+。
