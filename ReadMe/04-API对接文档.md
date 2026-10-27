# ISPmanager API 对接文档

本文面向管理端、客户门户与第三方集成。完整机器可读契约以运行环境为准：

- OpenAPI JSON/YAML：`GET /api/schema/`
- Swagger UI：`GET /api/docs/`
- ReDoc：`GET /api/redoc/`

Base URL 示例：`https://admin.example.com`（与门户可同域反代 `/api`）。

---

## 1. 通用约定

### 1.1 数据格式

- 请求 / 响应：`application/json`（上传部署包等为 `multipart/form-data`）
- 分页（列表默认）：

```json
{
  "count": 100,
  "next": "https://host/api/...?page=2",
  "previous": null,
  "results": []
}
```

默认 `page_size=20`，可通过 `page`、`page_size` 调整。

### 1.2 错误形态

统一异常处理器返回 HTTP 状态码，正文常见字段：

| 字段 | 说明 |
|------|------|
| `code` | 业务码（部分接口） |
| `message` / `detail` | 人类可读错误 |
| 字段名数组 | DRF 校验错误，如 `{"username":["用户名已存在"]}` |

常见状态：`400` 参数、`401` 未认证、`403` 无权限、`404` 不存在、`429` 限流。

### 1.3 过滤 / 搜索 / 排序

多数 ViewSet 支持：

- 精确过滤：`?status=active&service_type=vps`
- 搜索：`?search=关键字`
- 排序：`?ordering=-created_at`

### 1.4 权限码

后台接口除登录外，按模块校验 `xxx.view` / `xxx.manage` 或页面码（如 `accounts.role.view`）。  
客户门户接口使用 `IsCustomer`（必须已绑定 `Customer`），不校验后台菜单权限。

---

## 2. 认证

系统支持两种认证，可同时配置。

### 2.1 JWT（前端推荐）

**登录**

```
POST /api/auth/token/
Content-Type: application/json

{ "username": "admin", "password": "********" }
```

响应要点：

```json
{
  "access": "<jwt>",
  "refresh": "<jwt>",
  "user": { "id": 1, "username": "admin", "permissions": ["..."], "role": "superadmin" }
}
```

后续请求：

```
Authorization: Bearer <access>
```

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/api/auth/token/` | 登录，获取双 Token | 公开 |
| POST | `/api/auth/token/refresh/` | 刷新 Access（Refresh 轮换并拉黑旧值） | 公开（需 refresh） |
| POST | `/api/auth/token/verify/` | 校验 Token | 公开 |
| GET | `/api/auth/me/` | 当前用户资料与权限 | JWT |
| POST | `/api/auth/me/change_password/` | 修改自己密码 | JWT |
| GET | `/api/auth/users/` | 简易用户列表（内部） | JWT |

Token 寿命：Access **2 小时**，Refresh **7 天**。

### 2.2 API Token（自动化 / 集成）

在管理端用户详情生成，明文仅创建时返回一次。

```
Authorization: Token ispm_xxxxx
```

或：

```
X-API-Token: ispm_xxxxx
```

服务端存 SHA-256，支持过期、停用、按用户吊销。

### 2.3 门户注册（公开）

```
POST /api/portal/register/
```

| 字段 | 必填 | 说明 |
|------|------|------|
| username | 是 | 唯一 |
| password | 是 | ≥8，走 Django 密码策略 |
| email | 是 | |
| contact_name | 是 | |
| contact_phone | 否 | |
| company_name | 否 | 空则用联系人作客户名 |
| customer_type | 否 | `personal` / `enterprise` |
| promotion_code | 否 | 推广码 |

成功 `201`：返回 JWT `access`/`refresh`，并创建 `Customer` + 档案 `account_type=customer`、`role=customer`。

---

## 3. 账号与权限 `/api/accounts/`

管理员接口（`IsAdminUser` 或对应权限）。

### 3.1 用户

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/accounts/users/` | 用户分页列表，可筛 `search/is_active/account_type/role` |
| POST | `/api/accounts/users/` | 创建。系统账号选内部角色；客户账号必须 `customer_id`，角色仅 `customer`/`readonly` |
| GET/PATCH/DELETE | `/api/accounts/users/{id}/` | 详情 / 更新 / 删除 |
| POST | `/api/accounts/users/{id}/reset_password/` | `{new_password, confirm_password}` |
| POST | `/api/accounts/users/{id}/toggle_active/` | `{is_active}` |

用户对象关键字段：`username, email, role, role_label, account_type, customer_id, customer_name, permissions[], is_staff, is_superuser, is_active`。

### 3.2 角色权限

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/accounts/roles/` | 角色目录 + 权限组 + `action_meta` + `role_labels` |
| GET | `/api/accounts/roles/{role}/permissions/` | 该角色已授权码列表 |
| PUT | `/api/accounts/roles/{role}/permissions/` | `{permissions: ["service.manage", ...]}`；改 superadmin 需超级用户 |
| POST | `/api/accounts/permissions/reset-defaults/` | 恢复内置目录与默认授权（仅超级用户） |
| PATCH | `/api/accounts/permission-groups/{key}/` | 改模块名称/描述 |
| POST | `/api/accounts/permission-pages/` | 自定义页面 |
| PATCH/DELETE | `/api/accounts/permission-pages/{group}/{page}/` | 更新/删除页面 |
| POST | `/api/accounts/permissions/` | 自定义权限定义 |
| PATCH/DELETE | `/api/accounts/permissions/{code}/` | 更新/删除 |
| GET | `/api/accounts/me/permissions/` | 当前用户有效权限 |

`{role}` 取值：`superadmin` `admin` `customer_manager` `operator` `sales` `customer` `readonly`。

### 3.3 API Token

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/accounts/tokens/` | 当前用户令牌；创建需 `name`，可选 `expires_at` |
| GET/PATCH/DELETE | `/api/accounts/tokens/{id}/` | 查看 / 停用 / 删除 |
| POST | `/api/accounts/tokens/revoke_all/` | 吊销自己全部 |
| POST | `/api/accounts/tokens/verify/` | 校验某原始 Token |
| GET | `/api/accounts/admin/tokens/` | 管理员总览 |

---

## 4. 客户与账务 `/api/customer/`

标准 REST：`GET/POST /{resource}/`，`GET/PUT/PATCH/DELETE /{resource}/{id}/`。

| 资源 | 路径前缀 | 额外动作 |
|------|----------|----------|
| 客户 | `/customers/` | `GET {id}/logs/` 日志；`GET {id}/statistics/` 统计；`GET {id}/wallet-ledger/` 余额流水；`POST {id}/adjust-balance/` 调账 |
| 联系记录 | `/contact-records/` | |
| 合同 | `/contracts/` | `POST {id}/sign/` 签署 |
| 账单 | `/billings/` | 见下 |
| 支付流水 | `/payment-transactions/` | `GET {transaction_no}/` 未终态时自动向服务商查单；`POST {transaction_no}/reconcile/` 后台强制对账 |
| 操作日志 | `/operation-logs/` | 只读 |
| 渠道佣金 | `/channel-commissions/` | `summary`、`create-settlement`、`void` |
| 渠道结算 | `/channel-settlements/` | `commissions`、`confirm`、`mark-paid`、`cancel` |
| 渠道收款资料 | `/channel-payout-profiles/` | |
| 渠道账本 | `/channel-finance-ledgers/` | `summary`、`adjustment` |

### 4.1 账单动作

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/billings/online-payment-methods/` | 可用在线支付渠道 |
| GET | `/billings/{id}/payment-transactions/` | 该账单交易 |
| POST | `/billings/{id}/start-online-payment/` | 发起在线支付，返回 checkout |
| GET | `/billings/preview/` | 出账预览 |
| POST | `/billings/generate/` | 生成账单 |
| POST | `/billings/{id}/pay/` | 登记收款 / 支付 |
| POST | `/billings/{id}/request-refund/` | 发起退款（可 `force`、`reason`）；仅开通失败无实例或升配未执行可自动退 |
| POST | `/billings/{id}/complete-refund/` | 确认人工退款完成（`refunding` / `pending_manual` → `refunded`） |

### 4.2 支付回调（公开或网关调用）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/customer/payments/webhooks/{channel_id}/` | 支付渠道 Webhook |
| GET | `/api/customer/payments/return/{transaction_no}/` | 浏览器回跳 |

对接时按渠道配置 webhook URL，密钥存在通道加密配置中，响应勿回传私钥。

### 4.3 支付交易对账（需登录）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/customer/payment-transactions/{transaction_no}/` | 查询交易；未终态且距上次对账超过约 45 秒则主动查单入账。门户轮询走此接口 |
| POST | `/api/customer/payment-transactions/{transaction_no}/reconcile/` | 后台强制对账（系统账号 + `billing.manage`） |

---

## 5. 资源管理 `/api/resource/`

| 资源 | 前缀 | 说明 / 动作 |
|------|------|-------------|
| 大区 | `/regions/` | 只读地理 |
| 国家 | `/countries/` | 只读 |
| 城市 | `/cities/` | 只读 |
| 运营商 | `/carriers/` | `statistics` |
| POP | `/pop-points/` | 见 5.1 |
| 网卡 | `/interfaces/` | `push_to_node` |
| 路由表 | `/route-tables/` | |
| 静态路由 | `/static-routes/` | |
| 策略路由 | `/policy-routes/` | |
| IP 段 | `/ip-blocks/` | `preview_ips`、`generate_ips` |
| IP 地址 | `/ip-addresses/` | `assign`、`release`、`bulk_create`、`batch_import` |
| 隧道 IP 池 | `/tunnel-ip-pools/` | `usage`、`next-free`、`push_to_node` |
| VPS | `/vps/` | 见 5.2 |
| VPS 网卡 | `/vps-interfaces/` | |
| 边缘加速 | `/edge-accelerations/` | 见 5.3 |
| 带宽分配 | `/bandwidth-allocations/` | `statistics` |
| 资源日志 | `/operation-logs/` | 只读 |

### 5.1 POP 动作

`POST/GET /pop-points/{id}/{action}/`（具体方法以 Swagger 为准）：

`change_status`、`logs`、`bandwidth`、`set_bandwidth`、`resume-paid-provisioning`、`statistics`、`map-overview`、`test_ssh`、`upload_package`、`set_deploy_status`、`sync_from_node`、`push_to_node`、`pve_resources`、`create_vm`

`upload_package` 为文件上传，受 `POP_DEPLOY_MAX_UPLOAD` 限制。

POP 读写含 `api_verify_ssl`（默认 `false`）：节点按 IP / 自签证书同步时不要打开，否则 `sync_from_node` 会因 `CERTIFICATE_VERIFY_FAILED` 失败。关闭后仍走 HTTPS，只是不校验证书。

### 5.2 VPS 动作

`billing_preview`、`push_bandwidth`、`change_status`、`logs`、`ip_addresses`、`statistics`、`power`、`clone`、`vnc_ticket`、`sync_now`

电源 `power` 典型 body：`{"action": "start|stop|reboot"}`。  
`vnc_ticket` 返回票据，前端再连 WebSocket。

### 5.3 边缘加速动作

`billing_preview`、`change_status`、`logs`、`ip_addresses`、`protocol_config`、`statistics`、`push_to_node`、`wg_genkey`

`protocol_config` 按 `protocol` 读写 GRE / WG / Socks5 / VXLAN 字段。

---

## 6. 服务履约 `/api/service/`

| 资源 | 前缀 | 动作 |
|------|------|------|
| 工单 | `/orders/` | `approve` `submit` `assign_handler` `advance_stage` `delivery-options` `deliver` `retry-auto-provision` `manual-delivery` `manual-delivery/rotate-token` `manual-delivery/callback` `logs` `lifecycle` `changes` `snapshot` `statistics` `overview` |
| 咨询 | `/inquiries/` | `assign` `reply` `convert-to-order` `convert-to-ticket` |
| 技术工单 | `/tickets/` | `assign` `reply` `resolve` `close` `escalate` |
| 镜像目录 | `/os-images/` | CRUD |
| 快照档案 | `/snapshots/` | `delete` |
| 自助任务 | `/self-service-jobs/` | `complete` `fail` `cancel` |
| 实例 | `/instances/` | `activate` `suspend` `resume` `terminate` `convert_to_billing` `assign_ips` `release_ips` `logs` `allocations` `statistics` `fee_summary` |
| 分配记录 | `/allocations/` | 只读明细 |
| 变更 | `/changes/` | `approve` `execute` `complete` `rollback` `cancel` |
| 生命周期 | `/lifecycle/` | |
| 开通钩子 | `/hooks/` | 外部开通回调预留 |
| 统计 | `/stats/` | `by-customer` `by-pop` `by-type` |
| 计费 | `/billing/` | `preview` |

工单创建常见字段：`customer`、`product`、`business_type`（`vps/edge_gre/edge_wg/edge_socks5/ip/bandwidth`）、规格需求（`requested_vcpu` 等）、`quoted_price`。创建后只读返回 `fx_quote_currency`、`fx_locked_at`；详情另含 `fx_snapshot`（下单锁定的汇率表，支付换算与月结折算优先使用）。列表/详情另含 `capacity_reservation`（预占状态、规格、到期时间）与 `manual_delivery`（人开清单进度；门户不含令牌）。新开/升配在期望 POP 容量不足且未开超售时，创建接口返回 400，不落工单。

**人工交付回传** `POST /api/service/manual-delivery/callback/`：匿名，仅校验 `X-Delivery-Token`（或 body `token`）。可 `mark` 清单步骤；Socks5 可带 `socks5.create_edge` / `node_pushed` / `complete` 建档并完成交付。管理端 `GET /orders/{id}/manual-delivery/` 看步骤，`POST .../rotate-token/` 生成一次明文令牌。

技术工单创建字段：`customer`（后台必填）、`category`、`priority`、`subject`、`description`、可选 `related_instance` / `handler`。回复 body：`content`、`is_internal`、可选 `status`。门户创建不传 `customer`（取当前客户），回复只有 `content`，看不到 `is_internal=true` 的消息。SLA 扫描：`manage.py process_support_sla`。

实例暂停 / 终止需传 `reason`（以后端序列化为准）。

---

## 7. 产品 `/api/product/`

| 资源 | 前缀 | 动作 |
|------|------|------|
| 规格 | `/specs/` | `brief` |
| 模版 | `/templates/` | `brief` |
| 模版定价 | `/template-pricings/` | |
| 产品 | `/products/` | `capacity` `stock` `quote` `publish` `publish-logs` `from-template` `brief` `statistics` |
| 升降配规则 | `/upgrade-rules/` | |
| 发布日志 | `/publish-logs/` | |

**报价** `POST /products/{id}/quote/`：传入规格覆盖项，返回月费、币种、能力校验。门户同源逻辑在 `/api/portal/products/{id}/quote/`。

产品读写新增计费模式：`billing_mode`（`flat` / `95th` / `avg` / `transfer_quota`，默认包月）、`transfer_quota_gb`、`overage_unit_price`。列表/详情只读返回 `billing_mode_label`。流量包必须带额度；95 / 平均必须带承诺带宽。报价与门户下单快照会带上同一套计费档。月结仍出基础月租；归档流量报告后按计费模式生成独立超额账单（见下）。

**库存** `GET /products/{id}/stock/`：估算可开通数量，返回 `available`（已扣预占）与 `held`（当前预占）。容量校验已计入未开通工单的预占，避免下单到开通之间超卖。

POP 写字段新增 `allow_oversell`（默认 `false`）：打开后仍记账本，但允许预占超过剩余容量。

---

## 8. 供应商 `/api/supplier/`

| 资源 | 前缀 | 动作 |
|------|------|------|
| 供应商 | `/suppliers/` | `brief` `resources` `sync-logs` `statistics` |
| API 凭证 | `/apis/` | `test` `pull-resources` |
| POP 绑定 | `/pop-bindings/` | |
| 外部资源 | `/resources/` | `map` 映射到本地资源 |
| 同步日志 | `/sync-logs/` | |

密钥字段只写不回显。

---

## 9. 运维监控 `/api/operation/`

| 资源 | 前缀 | 动作 |
|------|------|------|
| Zabbix 服务器 | `/zabbix-servers/` | `test-connection` `host-groups` `templates` `sync` `sync-status` |
| 图形配置档 | `/graph-profiles/` | |
| 主机模版 | `/host-templates/` | |
| 监控主机 | `/monitoring-hosts/` | `sync` `monitoring-panel` |
| 监控项 | `/monitoring-items/` | `chart-data` |
| 监控图 | `/monitoring-graphs/` | `chart-data` |
| 图序列 | `/graph-series/` | |
| 图绑定 | `/graph-bindings/` | `resource-panel` |
| 流量报告 | `/traffic-reports/` | `host-graphs` `archive` `resource-context` `query` `percentile` |
| 同步日志 | `/sync-logs/` | |
| POP 品质 | `/pop-quality-monitors/` | `discover` `probe-now` `history` |
| 遥测凭证 | `/pop-telemetry-credentials/` | `rotate` |
| WebSSH 模版 | `/webssh-templates/` | |
| 品质上报 | `POST /edge-quality/ingest/` | 节点批量上报，按令牌限流 |
| WebSSH 解析 | `POST /webssh/resolve/` | 解析目标主机连接参数 |

流量 `query` 支持时间窗与 95 分位；门户侧会限制最长窗口。

**归档出账** `POST /traffic-reports/{id}/archive/`：报告标为已归档后，按产品计费模式尝试生成独立「流量超额」账单明细。响应在原列表字段上增加 `traffic_billing`（`status`=`billed`/`skipped`/`error`，以及 `reason`/`bill_no`/`amount`）。包月、超额为 0、单价为 0、无客户（如纯 POP 报告）会跳过。归档本身不因出账失败而回滚。列表/详情同样带只读 `traffic_billing`（已出账时才有账单号）。

补扫命令：`python manage.py generate_traffic_billing_items [--dry-run] [--report-id] [--bill-month YYYY-MM] [--no-archive] [--archive-only] [--include-open-month]`。日终默认先把上一自然月及更早的「已完成」报告归档，再出超额账。本月窗口不自动归档。

账单列表可按 `billing_kind=monthly|traffic_overage|wallet_recharge` 过滤；明细带 `traffic_report` / `traffic_report_no`。月结 `POST /billings/generate/` 只重建 `billing_kind=monthly` 的自动账单，不会覆盖流量超额单或余额充值单。

---

## 10. 系统 `/api/system/`

| 资源 | 前缀 | 动作 |
|------|------|------|
| 参数 | `/settings/` | `grouped` `categories` `currencies` `public` `bulk_update` |
| 总览 | `/overview/` | 仪表盘聚合 |
| 支付通道 | `/payment-channels/` | `providers` |
| IM 通道 | `/im-channels/` | `channel_types` `test` `send` |
| 通知规则 | `/notification-rules/` | `events` `preview` `dispatch` |
| IM 日志 | `/im-logs/` | |

### 10.1 统一操作日志 `/api/log/`

系统内部账号 + `system.log.view`。标准分页（`count` / `results`）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/log/audit-logs/` | 列表。筛选：`module` `action_type` `operator` `operator_name` `source` `target_model` `search` `created_at_after` `created_at_before` `date_from` `date_to` |
| GET | `/api/log/audit-logs/{id}/` | 详情（含 `detail` JSON） |
| GET | `/api/log/audit-logs/meta/` | 模块 / 操作类型 / 来源及模块条数 |

`module`：`accounts` `customer` `billing` `resource` `service` `product` `supplier` `operation` `system` `portal`。  
查看本接口的 GET **不会**再记一条审计。口令与 Token 不会出现在 `detail` 中。

**公开参数**（登录页可用，无需 Token）：

```
GET /api/system/settings/public/
```

返回 `is_public=True` 的键值（站点名、公司名、客服 QQ/微信/TG 等）。

支付 `providers` 当前包括 Stripe、PayPal 等（见 `payment_providers.py`），`encrypted_config` 只写。

---

## 11. 客户门户 `/api/portal/`

未标注「公开」的接口均需客户 JWT，且用户必须绑定 Customer。

### 11.1 公开

| 方法 | 路径 | 说明 | 限流 |
|------|------|------|------|
| POST | `/register/` | 注册 | 密码策略 |
| GET | `/products/` | 上架产品列表 | |
| GET | `/products/{id}/` | 产品详情 | |
| POST | `/products/{id}/quote/` | 报价 | |
| GET | `/products/{id}/delivery-options/` | 可交付节点、库存、预计开通时长 | |
| GET | `/locations/` | 可选地域 / POP | |
| GET | `/support-config/` | 对外客服（QQ/微信/TG/企微/邮箱/电话；不读 IM 告警目标） | |
| POST | `/support-inquiries/` | 官网咨询 / 申请测试 | `5/hour` |

`support-config` 响应：

```json
{
  "qq": "",
  "wechat": "",
  "wechat_qr": "",
  "telegram": "",
  "wecom_kf_url": "",
  "email": "",
  "phone": ""
}
```

只读系统设置「对外客服」公开项，**不会**回退 Telegram / 企微 IM 通道的 `default_target`。响应带 `Cache-Control: no-store`；服务端有 60 秒缓存，保存 `support_*` 后立即失效。

咨询 body：`contact_name, company_name, phone, email, preferred_contact, requirements, inquiry_type, source_url`。至少 phone 或 email。`inquiry_type`：`help` / `trial`。

### 11.2 登录后

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/dashboard/` | 总览统计 |
| GET/PATCH | `/profile/` | 客户资料 |
| GET | `/services/` | 我的服务（实例 + 未回填的直连资源；只读，不写库） |
| POST | `/services/{instance_id}/change_quote/` | 变更报价 |
| POST | `/services/{instance_id}/change_request/` | 提交变更（规格 / 续费 / 终止；续费与 `/renew/` 相同） |
| POST | `/services/{instance_id}/renew_quote/` | 一键续费报价（账期：月/季/半年/年） |
| POST | `/services/{instance_id}/renew/` | 一键续费出账并可选拉起支付 |
| GET | `/orders/` `/orders/{id}/` | 我的订单 |
| POST | `/orders/` | 下单 |
| POST | `/orders/direct-checkout/` | 下单并直接拉起支付 |
| GET | `/inquiries/` | 我的咨询（只读） |
| GET/POST | `/tickets/` | 技术工单列表 / 提交 |
| GET | `/tickets/{id}/` | 技术工单详情（不含内部备注） |
| POST | `/tickets/{id}/reply/` | 客户回复 |
| GET | `/os-images/` | 已上架系统镜像 |
| POST | `/vps/{id}/reinstall/` | 提交重装任务（`os_image`，可选 `password`） |
| GET | `/vps/{id}/snapshots/` | 快照列表 |
| POST | `/vps/{id}/snapshots/create/` | 提交创建快照 |
| POST | `/vps/{id}/snapshots/{sid}/restore/` | 提交恢复 |
| POST | `/vps/{id}/snapshots/{sid}/delete/` | 删除快照 |
| GET | `/vps/{id}/self-service-jobs/` | 该 VPS 自助任务 |
| GET | `/bills/` `/bills/{id}/` | 账单（含 `billing_kind`） |
| GET | `/bills/payment-methods/` | 在线支付方式 |
| GET | `/bills/wallet/` | 余额与近期流水 |
| POST | `/bills/recharge/` | 创建余额充值单 |
| POST | `/bills/{id}/pay/` | 在线支付账单 |
| POST | `/bills/{id}/pay-with-balance/` | 用账户余额抵扣（不可付充值单） |
| GET | `/vps/` `/vps/{id}/` | 我的 VPS |
| POST | `/vps/{id}/power/` | 电源 |
| GET | `/vps/{id}/vnc_ticket/` | VNC 票据 |
| POST | `/vps/{id}/set_root_password/` | 改 root 密码 |
| POST | `/vps/{id}/sync_now/` | 同步状态 |
| GET | `/vps/{id}/traffic/` | 流量（可 `time_from`/`time_till`，最长 7 天） |
| GET | `/edge/` `/edge/{id}/` | 我的边缘 |
| GET/PATCH | `/edge/{id}/protocol_config/` | 协议配置 |
| POST | `/edge/{id}/push_to_node/` | 下发 |
| POST | `/edge/{id}/wg_genkey/` | 生成 WG 密钥 |
| GET | `/edge/{id}/traffic/` | 流量 |
| GET | `/edge/{id}/quality/` | 品质 |

#### 变更报价 / 提交示例

```json
POST /api/portal/services/12/change_quote/
{
  "requested_vcpu": 4,
  "requested_ram_mb": 8192,
  "requested_disk_gb": 80,
  "requested_bandwidth_mbps": 100,
  "requested_ip_count": 1
}
```

```json
POST /api/portal/services/12/change_request/
{
  "requested_vcpu": 4,
  "requested_ram_mb": 8192,
  "requested_disk_gb": 80,
  "requested_bandwidth_mbps": 100,
  "channel_id": 1,
  "payment_method": "stripe",
  "requirements": ""
}
```

续费 / 终止：

```json
POST /api/portal/services/12/renew_quote/
{ "billing_cycle": "quarterly" }

POST /api/portal/services/12/renew/
{ "billing_cycle": "quarterly", "channel_id": 1 }

{ "change_type": "terminate", "requirements": "到期不续" }
```

续费按当前月费 × 账期月数出账，支付成功后延长 `expire_time`（并从到期日起算未过期实例）。兼容 `change_request` + `change_type=renew`。

升配 / 续费成功可能返回 `checkout`（跳转或二维码）或 `payment_required` + `billing`。

注意：路径中的 `{instance_id}` 是 **ServiceInstance 主键**，不是列表里的 `ins:12` 展示 ID。列表字段 `instance_id` 即此值。无实例的历史资源刷新列表后会自动生成。

---

## 12. WebSocket

连接需带 JWT，例如：

```
wss://host/ws/resource/vnc/{vps_id}/?token=<access>&vncticket=<ticket>&port=<port>
wss://host/ws/resource/pop-ssh/{pop_id}/?token=<access>
wss://host/ws/operation/webssh/{target_type}/{target_id}/?token=<access>
```

`target_type`：`pop` | `vps` | `monitoring_host` | `custom`。

生产经 nginx `/ws/` 反代到 ASGI。协议为二进制终端流（xterm / noVNC），不是 JSON RPC。

多 gunicorn worker 时请配置 Redis Channel Layer（`CHANNEL_REDIS_URL`），否则跨进程通道投递会失败。开发未配置则使用内存通道。

---

## 13. 管理端前端对接要点

- Axios base：`/api`
- 登录后把 `access_token` 写入 localStorage，请求拦截器加 Bearer
- 401 尝试 refresh，失败跳转 `/login`
- 路由 `meta.permission` 与用户 `permissions` 做包含/覆盖匹配（`manage` 可进 `view`）

门户 ISPweb 同样使用 `/api/auth/token/` 与 `/api/portal/*`。

---

## 14. 第三方集成建议

1. 使用 **API Token** 而非长期 JWT。
2. 只申请所需角色（建议独立集成账号 + 最小权限矩阵）。
3. Webhook 验签、幂等：`PaymentWebhookEvent` 会落库，重复通知应安全。
4. 不要依赖未文档化的 Admin-only 字段；以 Serializer / OpenAPI 为准。
5. 开通、变更以 **工单 / 实例 / 变更单号** 做对账，不要只认内部自增 ID。
6. 节点侧品质上报走 `/api/operation/edge-quality/ingest/` + 遥测凭证，勿复用用户 JWT。

---

## 15. 接口一览（按前缀）

| 前缀 | 模块 |
|------|------|
| `/api/auth/` | 登录与当前用户 |
| `/api/accounts/` | 用户、角色、Token |
| `/api/customer/` | 客户、合同、账单、支付、渠道 |
| `/api/resource/` | POP、IP、VPS、边缘、带宽 |
| `/api/service/` | 工单、实例、变更、咨询、技术工单、自助任务 |
| `/api/product/` | 产品目录与报价 |
| `/api/supplier/` | 上游供应商 |
| `/api/operation/` | 监控、流量、WebSSH |
| `/api/system/` | 参数、支付通道、IM |
| `/api/log/` | 统一操作日志 |
| `/api/portal/` | 客户自助 |
| `/api/docs/` `/api/schema/` | 文档 |
| `/ws/` | 实时终端 |

更细的字段级 schema 请打开 `/api/docs/` 或导出 `/api/schema/`。
