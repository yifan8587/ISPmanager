# 对接检查：ISPmanager 资源管理 / 指令下发 → vpopmanager

**日期**：2026-08-23  
**范围**：`ISPmanager` 中心（资源管理、开通、门户下发）↔ `vpopmanager` 节点（原 pemanager）  
**方法**：通读两边源码、路由、客户端与测试；交叉核对 HTTP 路径与 apply 开关。未对真实 POP 做联机探测。  
**目的**：先把「配置能否下到节点、服务能否跟着生效、现有测试能否证明这一点」写清楚，供后续优化。

---

## 1. 结论

主链路在**协议层已经对齐**：ISPmanager 用 `PemanagerClient` 登录节点 JWT，按「写 desired-* → `apply-system` / 专用 action」把路由、隧道、IP、带宽、PVE 操作推到 vpopmanager；节点再写 netplan / wg / `ip` / `tc` / PVE API。

**测试不能证明「中心下发 → 节点执行」整条链路。** 两边都没有用真实或录制的节点 HTTP 做端到端用例：ISPmanager 只 mock 了少量 payload / SSL；vpopmanager 的 apply 路径多数停在服务层单元测试，路由 / QoS / 防火墙几乎无测。因此：**代码契约大体可对接，运行时是否真执行取决于节点开关与人工二次确认，当前测试覆盖不到。**

| 维度 | 状态 |
|------|------|
| REST 路径与方法 | **对齐**（见 §3） |
| 隧道 / 路由 payload | **基本对齐**（`spec.netplan_tunnel`） |
| 鉴权 | **能通**：JWT 登录，或 `PopPoint.mgmt_token`（`pem_*`） |
| 配置真正落到系统 | **有条件**：生产 env 默认关闭本机写入 |
| 服务联动（开通 GRE/WG、VPS 模板） | **代码已接**；依赖节点 PVE/网络开关 |
| 现有测试证明联动 | **不能** |

---

## 2. 角色与数据流

```
ISPmanager（中心）                         vpopmanager（节点 POP / PVE）
┌─────────────────────────┐               ┌──────────────────────────────┐
│ PopPoint.api_* 凭据      │  JWT login    │ /api/accountmanage/auth/login │
│ deploy_service / ip_ops  │──────────────►│ desired-* + apply-system      │
│ pve_ops / sync_service   │  Bearer JWT   │ allocate / QoS / PVE API      │
│ 门户 / 自动开通          │◄──────────────│ GET 现场：接口/路由/IP/VM     │
└─────────────────────────┘               └──────────────┬───────────────┘
                                                         │ apply 开关打开时
                                                         ▼
                                              netplan / wg-quick / ip / tc / PVE 8006
                                                         │
                                              品质遥测（出站，非下发）
                                              X-POP-Telemetry-Token
                                              → ISPmanager /api/operation/edge-quality/ingest/
```

- **vpopmanager** 是部署在节点上的完整 Django + Vue 平台，不是独立 agent 守护进程。生产可由 nginx 反代 `/api/`、`/ws/`（`--mode backend` 可无 UI）。
- ISPmanager 文档与客户端仍称 **pemanager**；节点仓库已 rebrand 为 vpopmanager，路径与旧 `99-pemanager-*.yaml` 并存。
- 节点**没有** `/agent/*` 专用协议；契约就是 `deploy/API.md` + 实际 `urls.py`。

连接参数（ISPmanager `PopPoint`）：

| 字段 | 用途 |
|------|------|
| `api_base_url` 或 `http://{mgmt_ip}` | 节点 API 基址 |
| `api_username` / `api_password`（缺省回退 mgmt 账号） | JWT 登录 |
| `mgmt_token` | 长期 Token（`pem_*`）；N1 起 `from_pop()` / `authenticate()` 会使用 |

TLS：优先 `PopPoint.api_verify_ssl`（默认关闭，适配自签证书与按 IP 访问）。未带 POP 时：HTTPS + IP 自动不校验；域名则跟 `PEMANAGER_SSL_VERIFY`（可用 `DJANGO_PEMANAGER_SSL_VERIFY` 覆盖；DEBUG 默认关）。

---

## 3. 指令对照（中心调用 ↔ 节点路由）

下列路径均在 vpopmanager `urls.py` 中存在，与 `PemanagerClient` 一致。

### 3.1 鉴权与探活

| ISPmanager 调用 | 节点路径 | 节点行为 |
|-----------------|----------|----------|
| `login()` | `POST /api/accountmanage/auth/login/` | 返回 `{access, refresh, user}` |
| `health()` | `GET /api/accountmanage/health/` | AllowAny |

后续请求：`Authorization: Bearer <access>`。节点另支持 `pem_<prefix>.<secret>`（Bearer 或 `X-API-Key`）；N1 起中心用 `mgmt_token`。Access 默认 8 小时（`PEM_ACCESS_TTL_HOURS`）；**N5** 起长同步遇 401 会 refresh，失败则重新 login。

写操作多数要求节点 **admin**（403 时客户端提示换管理员账号）。

### 3.2 拉取现场（同步入库）

| 客户端 | 路径 | 解包 |
|--------|------|------|
| `interfaces()` | `GET /api/interfacemanage/interfaces/` | `interfaces` |
| `system_routes(table=all)` | `GET /api/routemanage/system-routes/` | `routes` |
| `system_rules()` | `GET /api/routemanage/system-rules/` | `rules` |
| `ip_addresses()` | `GET /api/resourcemanage/ip-addresses/` | 列表（节点默认无分页） |
| `bandwidth_allocations()` / `bandwidth_pools()` | `/api/resourcemanage/bandwidth-*` | 列表 |
| `customers()` | `GET /api/resourcemanage/customers/` | 列表 |
| `tunnel_ip_pools()` | `GET /api/resourcemanage/tunnel-ip-pools/` | 列表 |
| `vms()` | `GET /api/pvemanager/vms/` | 无模块时 404 → `[]` |

入口：`POST /api/resource/pop-points/{id}/sync_from_node/`（总是打节点）。

### 3.3 配置下发（desired → apply）

| 中心对象 | 写意图 | 生效 |
|----------|--------|------|
| `StaticRoute` | `POST/PATCH /api/routemanage/desired-routes/` | `POST …/desired-routes/apply-system/` `{phase, ids?, persist_to_netplan?}` |
| `PolicyRoute` | `POST /api/routemanage/policy-rules/` | `POST …/policy-rules/apply-system/` |
| `EdgeAcceleration` GRE/VXLAN/WG | `POST/PATCH /api/interfacemanage/db/desired-tunnels/` | 新建：`…/apply-system/` `{ids}`；变更：`…/{id}/reconfigure/` |
| WG 路由 | — | `POST …/desired-routes/apply-wireguard/` |
| 物理/托管接口 | — | `POST /api/interfacemanage/interfaces/{ifname}/apply-config/` |
| 删托管接口 | — | `POST …/remove-config/` 或 DELETE desired-tunnel |
| 客户 | `POST/PATCH /api/resourcemanage/customers/` | 仅落节点 DB |
| 边缘品质监控 | `POST/PATCH /api/operationmanage/monitor-targets/` 等 | 节点本地采样；回传走遥测 |

边缘隧道 payload 由 `_build_tunnel_spec()` 生成，约定与节点 `DesiredTunnelConfig` 一致：`kind` + `spec.netplan_tunnel`（GRE/VXLAN/WG）。Socks5 **不走**隧道通道，自动开通会失败并转人工交付。

### 3.4 IP / 带宽 / PVE

| 中心 | 节点 |
|------|------|
| 批量录入 | `POST /api/resourcemanage/ip-addresses/actions/bulk-create/` |
| 分配 | `…/actions/allocate/` |
| 分配 + 路由 | `…/actions/allocate-with-route/` |
| 释放 | `…/actions/release/` |
| 带宽 upsert + 可选 tc | `POST /api/resourcemanage/bandwidth-allocations/actions/upsert/`（`apply_qos`） |
| VPS 网卡限速 | `POST /api/pvemanager/vms/allocate_bandwidth/`（写 PVE `netN rate=`，key 形如 `vm-{pk}-net0`） |
| 创建 / 模板 / 电源 / 克隆 / 删除 | `/api/pvemanager/vms/create_vm/`、`create_from_template/`、`{id}/{start\|stop\|restart\|shutdown}/`、`clone/`、`DELETE {id}/` |
| 同步 VM | `POST /api/pvemanager/vms/sync/` |
| VNC | `GET …/vnc_ticket/` + `ws://<node>/ws/pvemanager/vnc/<vm_pk>/?token=<JWT>` |

`reserve_ip()` / `delete_desired_route()` 仅在客户端定义，业务层未引用。

---

## 4. 配置与服务联动（是否会真执行）

### 4.1 节点「意图已写入」≠「系统已生效」

vpopmanager 的 apply 受环境开关约束。`deploy/templates/vpopmanager.env.example`（PVE 默认安装）**全部为 0**：

- `INTERFACEMANAGE_NETPLAN_WRITE_ENABLED`
- `INTERFACEMANAGE_WG_APPLY_ENABLED`
- `ROUTEMANAGE_NETPLAN_WRITE_ENABLED` / `IPROUTE_WRITE_ENABLED` / `IPRULE_WRITE_ENABLED`
- `QOSMANAGE_APPLY_ENABLED`
- `FIREWALLMANAGE_APPLY_ENABLED`

Django settings 里这些开关默认是 `1`，**以 `/etc/vpopmanager/vpopmanager.env` 为准**。开关关闭时：desired 仍写入 SQLite，apply 返回错误或只改库（reconfigure 会带 warning）。表现为：ISPmanager 显示「已下发」，节点界面有记录，**本机网卡 / 路由 / 限速不变**。

PVE 生命周期不走上述开关，走 proxmoxer → 宿主机 `:8006`。VM 带宽依赖 PVE 连通，不依赖 netplan 开关。

### 4.2 中心 dry_run 与「看起来已下发」

| 入口 | 默认 | 含义 |
|------|------|------|
| 管理端 `pop-points/{id}/push_to_node/` | `dry_run=true` | 只出计划；前端 `useConfirmPush` 确认后再 `false` |
| 管理端 Edge / 隧道池 `push_to_node` | `dry_run=true` | 同上 |
| 管理端 VPS `power` / POP `create_vm` | `dry_run=true` | 同上 |
| 接口 `interfaces/{id}/push_to_node/` | **无 dry_run** | 直接 apply-config |
| IP assign / release / bulk_create | `push_to_node=true` | **直接打节点**，无预览 |
| VPS `push_bandwidth` | 直接 | `allocate_vm_bandwidth` |
| 门户 Edge `push_to_node` | API 默认 `dry_run=false`；**N2** 前端先预览再确认执行 | 取消预览则不写节点 |
| 门户 VPS `power` | **`dry_run=false`** | 已有确认框，一点确认即执行 |
| 自动开通 GRE/WG | `push_pop_to_node(..., dry_run=False)` | 工单履约真下发 |
| 自动开通 VPS | `pve_ops.create_*(..., dry_run=False)` | 真创建 |
| 工单分配 IP | `_allocate_order_ips` | **N1**：POP 配了 API（Token 或账号）则 push 节点；失败回滚并转人工 |

成功且 `dry_run=false` 的 POP 级下发会清 `PopPoint.config_dirty`。只改本地边缘而不点「下发」时，中心脏、节点旧。**N3** 带宽分配、**N4** 静态/策略路由 CRUD 默认会尝试 push 节点（可关）。

### 4.3 已联动 / 未联动

| 能力 | 联动情况 |
|------|----------|
| GRE / VXLAN / WG 边缘 | 开通与 POP 下发会写 desired-tunnels 并 apply / reconfigure |
| 静态 / 策略路由 | **N4** 本地 CRUD 默认 `push_to_node`；无出接口/无匹配条件/无 API 则跳过。整 POP 下发仍可用 |
| VPS 创建 / 电源 | `pve_ops` ↔ pvemanager；门户电源直接执行 |
| VPS 带宽 | `allocate_bandwidth` 改 PVE rate |
| 边缘带宽 | upsert + `apply_qos` → 节点 tc（再受 `QOSMANAGE_APPLY_ENABLED` 约束） |
| IP 手工分配 | 默认 `push_to_node`；Edge 可带路由 |
| 隧道 p2p 池 | **N6** 本地 CRUD 默认 `push_to_node`；专用按钮仍先 dry_run 预览 |
| 品质监控 | 下发 monitor-target（`isp-edge-quality-{edge.pk}`）；节点 `upload_edge_telemetry` 回传 |
| Socks5 | **不自动下发** |
| 防火墙 / 安全组 | 节点有 firewallmanage；**中心无下发** |
| 重装 / 快照 / resize | 中心仍是骨架，**不调**节点 PVE |
| 升配规格 | 改本地实例，**不调** PVE 硬件 |

---

## 5. 现有测试能证明什么

### 5.1 ISPmanager

| 文件 | 与节点相关的内容 | 能否证明执行 |
|------|------------------|--------------|
| `resource_manager/tests_pemanager_ssl.py` | `PemanagerClient.verify` 跟随设置 | 否（只测 TLS 标志） |
| `resource_manager/tests.py` | mock 测 `_build_tunnel_spec` MTU、遥测 target 命名 | 否（无 HTTP） |
| `resource_manager/tests_node_contract.py` | **N1**：Token 鉴权、login→desired-tunnel→apply、`interpret_node_write`、`apply_ok` 不清 dirty、工单 IP mock | 假节点契约，非真机 |
| `resource_manager/tests_n3_bandwidth_push.py` | **N3**：带宽分配默认 push / 跳过 / 回滚 | 否（mock） |
| `resource_manager/tests_n4_route_push.py` | **N4**：静态/策略路由默认 push / 跳过 / 回滚 | 否（mock） |
| `resource_manager/tests_n5_jwt_refresh.py` | **N5**：401 → refresh / 重登；pem_* 不 refresh | 否（mock） |
| `resource_manager/tests_n6_tunnel_pool_push.py` | **N6**：隧道池默认 push / 跳过 / 回滚 | 否（mock） |
| `portal/tests_s2_hardening.py` | patch `pve_ops.vm_power` 测审计 | 否 |
| `service_manager/tests.py` | patch `auto_provision`；无节点 POP 的 IP 分配仍过 | 否（不进节点） |

**仍缺：** 真 POP 联机；`sync_pop_from_node` / `pve_ops` HTTP mock；录制完整 apply `steps`。

### 5.2 vpopmanager

| 模块 | 现状 |
|------|------|
| `interfacemanage/tests.py` | digest、GRE live、WG restart、netplan 模式等服务层测试 |
| `interfacemanage/tests_apply_api.py` | **N1**：`apply-system` 开关关闭 → HTTP 400 + `ok:false` |
| `resourcemanage/tests.py` | IP 释放清路由 + persist |
| `resourcemanage/tests_allocate_api.py` | **N1**：`allocate`（`interface_code` 可空） |
| `pvemanager/tests.py` | 权限、job、provision 逻辑；无 `allocate_bandwidth` HTTP |
| `operationmanage/tests.py` | 遥测上传 mock 到 ISP；无 monitor-target CRUD 对接中心 |
| `routemanage/tests.py` | **N1**：全量 `apply-system` 开关关闭 → HTTP 400 + `ok:false` |
| `qosmanage` / `firewallmanage` | **tests 空或几乎空** |

**缺失：** 用 ISPmanager 真实 payload（desired-route / desired-tunnel / allocate-with-route / allocate_bandwidth）打节点测试客户端再断言 apply 步骤。

因此：**「从 ISPmanager 资源管理与指令下发，能正常对接到 vpopmanager 执行」这一点，现有自动化测试无法确认。** 联调仍依赖人工：管理端二次确认下发 + 节点开关打开 + 看 `steps` / 本机 `ip`/`wg`/`tc`。

---

## 6. 风险与缺口（供后续调整）

### P0 — 运行时「以为下发了」

1. **节点 apply 开关默认关**（PVE 包）。N1 后中心区分 `rest_ok` / `apply_ok`；开关仍须在节点打开，否则 `apply_ok=false`、`config_dirty` 保留。
2. **无真机端到端测试**。N1 补了假节点 HTTP mock 与节点 apply/allocate API 用例，仍不能证明本机 `ip`/`wg`/`tc` 已变。
3. **工单 IP**：N1 起有 API 配置则 push 节点；无配置仍只改本地。

### P1 — 契约与操作不一致

4. **管理端默认 dry_run**；门户隧道 **N2** 先预览再执行。开通与门户电源仍默认真执行。
5. **长期凭据**：中心仍可存节点管理员密码；N1 已接 `pem_*`（`mgmt_token`）。
6. **JWT refresh（N5）**：access 收到 401 时先 `auth/refresh/`，失败再重新 `login()`。长期 `pem_*` Token 不走 refresh。
7. 中心**不下发防火墙**；节点防火墙与中心安全策略两套账。
8. 带宽本地 CRUD：**N3** 默认 `push_to_node=true`（VPS 同步则 `allocate_vm_bandwidth`，边缘有 `node_ifname` 则 upsert + QoS）。未同步 / 无接口 / 无 API 则跳过不挡建档；`push_to_node=false` 仅改本地。VPS 详情「仅保存 / 保存并下发」默认仍不自动 push。

### P2 — 产品骨架（已知，不单是对接 bug）

9. 重装 / 快照 / resize / 升配不调 PVE。
10. Socks5 仅人开。
11. 客户端仍按 pemanager 命名；文档 `deploy/API.md` 与代码有少量漂移（如部分 tools 路径）。

---

## 7. 建议优化顺序

N1（2026-08-23）已落地 §7.1–7.3 的代码与测试，详见 `31`。仍建议：

1. **契约测试（N1 已做假节点 / apply / allocate）**  
   未做：真 POP 联机、`allocate_bandwidth` HTTP、录制完整 `steps`。

2. **部署与观测（N1 已做 `rest_ok` / `apply_ok`）**  
   节点必须打开的 `*_WRITE_ENABLED` 见 `31`。生产 env 模板仍默认关。

3. **联动补洞（N1–N6：工单 IP、门户预览、带宽、路由、JWT refresh、隧道池）**  
   API 默认仍是真下发；门户前端先 `dry_run` 预览。带宽 / 路由 / 隧道池保存删除默认同步节点。

4. **明确不做（除非单独立项）**  
   防火墙中心化、重装/快照真调 PVE、Socks5 自动隧道。

---

## 8. 关键入口

| 主题 | 路径 |
|------|------|
| 中心客户端 | `ISPmanager/backend/resource_manager/pemanager_client.py` |
| 下发 | `…/deploy_service.py` |
| 同步 | `…/sync_service.py` |
| IP | `…/ip_ops.py` |
| PVE | `…/pve_ops.py` |
| 开通 | `ISPmanager/backend/service_manager/provisioning.py` |
| 节点路由 | `vpopmanager/backend/config/urls.py` |
| 节点契约文档 | `vpopmanager/deploy/API.md` |
| 节点写入开关 | `vpopmanager/deploy/templates/vpopmanager.env.example` |
| 节点 apply | `interfacemanage/views.py`、`routemanage/views.py`、`resourcemanage/views.py`、`pvemanager/views.py` |

---

N1–N5 已确认。N6 隧道 IP 池自动下发，详见 `36`。
