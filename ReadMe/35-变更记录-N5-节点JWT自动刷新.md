# 变更记录 N5 — 节点 JWT 自动刷新

**阶段**：节点对接（依据 `30`）  
**步骤**：N5  
**日期**：2026-08-23  
**对应**：`30` P1-6  
**前置**：N4 已确认

---

## 功能更新

1. **保存 refresh**：`login()` 同时记下节点返回的 `refresh`。
2. **401 自动续期**：GET / 写操作遇到 HTTP 401 时，先 `POST /api/accountmanage/auth/refresh/` 换新 access，再重试一次。
3. **refresh 失败则重登**：仍有账号密码时重新 `login()`；两者都失败才报错。
4. **长期 Token 不动**：`pem_*` / `mgmt_token` 不走 refresh，401 直接失败（与 N1 一致）。

未做：防火墙中心化、真 PVE 重装/快照、门户电源改 dry_run。

---

## 验证

```bash
cd ISPmanager/backend
/root/ISPmanager/venv/bin/python manage.py test \
  resource_manager.tests_node_contract \
  resource_manager.tests_n5_jwt_refresh --keepdb
```

---

## 下一步

N5 已确认。N6：隧道 IP 池保存时默认下发节点。
