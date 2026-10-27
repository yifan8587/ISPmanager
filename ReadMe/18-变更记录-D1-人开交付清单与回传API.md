# 变更记录 D1 — Socks5 等人开交付清单 + 回传 API

**阶段**：D（P1 交付与客服）  
**步骤**：D1  
**日期**：2026-08-23  
**对应建议**：`05` 第 2.5 节「Socks5 / 部分边缘协议今天显式拒绝自动开通 → 人工交付清单 + 状态回传 API」

---

## 功能更新

1. 无法自动下发时不再把开通状态留在 `none`（看起来像没触发），改为 `paid_pending`，并生成 **人工交付清单**：
   - `socks5`：建档 → 配置认证 → 节点确认 → 绑定实例
   - `multi_instance`：逐项选资源 → 后台交付
   - `bandwidth`：绑定带宽目标 → 后台交付
   - `pop_manual`：手动节点备资源 → 后台交付
2. 管理端工单详情 / 列表带 `manual_delivery`（进度、原因、是否未完成）。门户只读同一进度，**不含**回传令牌。
3. **状态回传 API**（令牌与 JWT 隔离，原始令牌只存 SHA-256）：
   - 管理端：`POST /api/service/orders/{id}/manual-delivery/rotate-token/` 生成一次明文令牌
   - 节点/运维：`POST /api/service/manual-delivery/callback/`，头 `X-Delivery-Token` 或 body `token`
   - 管理端也可 `POST /api/service/orders/{id}/manual-delivery/callback/`
4. Socks5 回传可建档（边缘资源 + Socks5Config）、确认 `node_pushed`，并 `complete=true` 创建实例、激活工单。其它类型回传只更新清单，仍走现有「资源交付」。
5. 后台「资源交付」成功后自动关闭清单。同一报告式幂等：已完成再回传 `complete` 不重复建实例。
6. 未付清预付账单的工单只返回「不自动开通」，不生成清单，避免未付款被标成「已付款待人工」。

本步不做开通失败自动重试分类、cloud-init / ISO、客户自助填凭证。

---

## 变更文件

| 文件 | 变更 |
|------|------|
| `backend/service_manager/models.py` | `ServiceManualDelivery` |
| `backend/service_manager/migrations/0015_manual_delivery.py` | 迁移 |
| `backend/service_manager/manual_delivery.py` | 清单、令牌、回传、Socks5 完成交付 |
| `backend/service_manager/provisioning.py` | 人开路径改 `paid_pending` 并落清单 |
| `backend/service_manager/views.py` / `urls.py` | 清单查询、轮换令牌、回传；匿名 callback |
| `backend/service_manager/serializers.py` / `admin.py` | 工单带清单摘要 |
| `backend/portal/serializers.py` / `views.py` | 门户只读进度 |
| `frontend/src/views/service/OrderList.vue` | 清单步骤、生成回传令牌 |
| `ISPweb/src/views/console/Orders.vue` | 显示清单进度 |

---

## 验证

```bash
cd ISPmanager/backend
python manage.py migrate
python manage.py test service_manager.tests_manual_delivery service_manager.tests --keepdb
```

零金额 Socks5 工单审批后应出现「已付款待人工」和 4 步清单，而不是「未触发」。管理端生成回传令牌后，节点 POST callback（建档 + `node_pushed` + `complete`）应开通实例。再回传不应重复建实例。后台选已下发边缘点「资源交付」也应关闭清单。

回传示例：

```bash
curl -X POST http://127.0.0.1:8000/api/service/manual-delivery/callback/ \
  -H 'Content-Type: application/json' \
  -H 'X-Delivery-Token: mdt_...' \
  -d '{"note":"节点已配置","node_pushed":true,"socks5":{"create_edge":true},"complete":true}'
```

---

## 下一步（需确认）

**D2**：客户技术工单（与服务工单分离）SLA 字段。

请回复：`确认 D1，继续 D2`。
