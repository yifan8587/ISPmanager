# 变更记录 C1 — POP 容量账本与下单预占

**阶段**：C（P0 库存与流量）  
**步骤**：C1  
**日期**：2026-08-23  
**对应建议**：`05` 第 2.3 节「库存账本：下单锁库存（预占）→ 开通消耗 → 失败回滚；禁止超卖或显式允许超售」

---

## 功能更新

1. 新增 `PopCapacityReservation` 账本：按 POP × 工单记录 vCPU / 内存 / 硬盘 / 带宽 / 公网 IP 预占。
2. 状态：`held`（预占）→ `consumed`（开通/交付消耗）→ `released`（驳回、开通失败、未付超时释放）。同一工单同时只保持一条 `held`。
3. 下单即锁库存：门户下单、后台建单、咨询转工单、升配增量。续费 / 降配 / 终止不预占。数量 `requested_quantity` 按份数放大。
4. `pop_available` / `check_spec_against_pop` / `estimate_stock` **计入预占**。开通校验排除本单已锁库存，避免自己卡自己。
5. 默认禁止超卖：预占失败则拒单并回滚，不占账本。POP 打开 `allow_oversell` 后仍记账本，但允许超过剩余容量。
6. 未付预占默认 72 小时过期（系统参数 `capacity_reservation_hold_hours`）。付款或零金额履约后取消超时；命令 `release_expired_reservations` 补扫。
7. 开通失败（`FAILED`，含履约异常）回滚预占。`INSUFFICIENT` / 人工待交付 **保持预占**，给补库存重试留位子。
8. 门户新增 `GET /api/portal/products/{id}/delivery-options/`：可交付节点、库存、预计开通时长。本步不做供应商库存合并视图，也不做完整交付 SLA 产品。

无预占的历史工单行为不变。无 IP 地址池时 IP 维度视为不限制（与 CPU 未填一致）；有地址池则按可用数减预占。

---

## 变更文件

| 文件 | 变更 |
|------|------|
| `backend/resource_manager/models.py` | POP `allow_oversell` |
| `backend/resource_manager/migrations/0025_poppoint_allow_oversell.py` | 迁移 |
| `backend/service_manager/models.py` | `PopCapacityReservation` |
| `backend/service_manager/migrations/0014_pop_capacity_reservation.py` | 迁移 |
| `backend/service_manager/capacity_reservation.py` | 预占 / 消耗 / 释放 / 过期扫描 |
| `backend/product_manager/services.py` | 可用容量扣预占 |
| `backend/portal/serializers.py` 等下单入口 | 创建时预占，失败拒单 |
| `backend/service_manager/provisioning.py` | 成功消耗、失败释放 |
| `backend/service_manager/management/commands/release_expired_reservations.py` | 超时释放命令 |
| `backend/portal/views.py` | 门户 `delivery-options` |
| `frontend/src/views/service/OrderList.vue` | 列表显示预占状态 |

---

## 验证

```bash
cd ISPmanager/backend
python manage.py test service_manager.tests_capacity_reservation service_manager.tests service_manager.tests_fx_snapshot service_manager.tests_change_billing --keepdb
```

在容量有限的 POP 上下两笔相同规格门户订单：第一笔应预占成功，第二笔应提示库存不足且不落工单。驳回第一笔或跑 `release_expired_reservations` 后，库存应回到可售。

---

## 下一步（需确认）

**C2**：产品计费模式字段（flat / 95 / 流量包）。

请回复：`确认 C1，继续 C2`。
