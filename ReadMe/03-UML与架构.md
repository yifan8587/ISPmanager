# ISPmanager UML 与架构

本文用 UML / 架构图描述系统边界、模块依赖、核心实体关系与关键业务状态机。表字段详见 [02-数据库表结构.md](./02-数据库表结构.md)。

## 1. 系统上下文

```mermaid
flowchart LR
    Admin["运营人员<br/>管理端 SPA"]
    Customer["终端客户<br/>ISPweb 门户"]
    Node["POP 节点<br/>Linux / PVE"]
    Zbx["Zabbix"]
    Pay["支付网关<br/>Stripe / PayPal / 微信等"]
    IM["IM 通道<br/>TG / 企微 / 钉钉"]
    Up["上游供应商 API"]
    API["ISPmanager 后端<br/>Django + DRF + Channels"]
    DB[(MySQL 8)]

    Admin -->|JWT /api| API
    Customer -->|JWT /api/portal| API
    API --> DB
    API -->|SSH / 部署 / 配置下发| Node
    API -->|WS VNC / WebSSH| Admin
    Customer -->|WS VNC| API
    API -->|监控同步 / 查图| Zbx
    API -->|Checkout / Webhook| Pay
    API -->|事件推送| IM
    API -->|拉资源 / 映射| Up
    Node -->|品质遥测| API
```

## 2. 部署架构

```mermaid
flowchart TB
    subgraph edge [nginx]
      A[admin.example.com<br/>管理端 dist]
      P[portal.example.com<br/>ISPweb dist]
      S["/api /ws /admin /static /media"]
    end
    GW["gunicorn + uvicorn<br/>backend.asgi:application"]
    DB[(MySQL)]
    Redis[(Redis<br/>可选 Channel Layer)]
    A --> S
    P --> S
    S --> GW
    GW --> DB
    GW -.-> Redis
```

- HTTP 与 WebSocket 共用 ASGI。
- 前端 History 路由：`try_files ... /index.html`。
- 静态与媒体由 nginx 直出，不经 Django。
- 未设 `CHANNEL_REDIS_URL` 时通道层为进程内存；生产多 worker 建议 Redis。

## 3. 后端应用模块依赖

```mermaid
flowchart TB
    portal[portal]
    accounts[accounts]
    customer[customer_manager]
    resource[resource_manager]
    service[service_manager]
    product[product_manager]
    supplier[supplier_manager]
    operation[operation_manager]
    system[system_manager]

    portal --> accounts
    portal --> customer
    portal --> service
    portal --> product
    portal --> resource
    portal --> system

    service --> customer
    service --> resource
    service --> product
    service --> system

    customer --> accounts
    customer --> system
    resource --> supplier
    operation --> resource
    operation --> customer
    accounts --> system
```

`log_manager` 已安装但无模型，审计实际落在各业务 `*OperationLog` / `IMMessageLog`。

## 4. 核心实体关系（概念 ER）

```mermaid
erDiagram
    User ||--|| UserProfile : profile
    User ||--o| Customer : "Customer.account"
    Customer ||--o{ ServiceOrder : orders
    Customer ||--o{ CustomerBilling : bills
    Customer ||--o{ Contract : contracts
    Customer ||--o{ ChannelCommission : commissions

    Product ||--o{ ServiceOrder : quoted
    ProductTemplate ||--o{ Product : from
    ProductSpec ||--o{ ProductTemplate : spec

    ServiceOrder ||--o{ ServiceInstance : instances
    ServiceInstance ||--o| VPS : vps
    ServiceInstance ||--o| EdgeAcceleration : edge
    ServiceInstance ||--o{ ServiceChangeRecord : changes
    ServiceInstance ||--o{ ServiceAllocation : allocations

    PopPoint ||--o{ VPS : hosted
    PopPoint ||--o{ EdgeAcceleration : hosted
    PopPoint ||--o{ IPBlock : blocks
    IPBlock ||--o{ IPAddress : ips
    VPS ||--o{ IPAddress : assigned
    EdgeAcceleration ||--o| GREConfig : gre
    EdgeAcceleration ||--o| WireguardConfig : wg
    EdgeAcceleration ||--o| Socks5Config : socks5

    CustomerBilling ||--o{ BillingItem : items
    CustomerBilling ||--o{ PaymentTransaction : txs
    PaymentChannel ||--o{ PaymentTransaction : channel

    UserProfile }o--|| RoleGrant : "role + RolePermissionGrant"
    PermissionDefinition ||--o{ RolePermissionGrant : grants
```

说明：角色不是独立表，`UserProfile.role` 为枚举字符串，授权行在 `accounts_rolepermissiongrant`。

## 5. 账号与客户绑定

```mermaid
classDiagram
    class User {
      username
      password
      is_staff
      is_superuser
      is_active
    }
    class UserProfile {
      account_type : system|customer
      role : superadmin|admin|...|customer|readonly
      phone
    }
    class Customer {
      name
      customer_type
      status
      account : OneToOne User
    }
    User "1" --> "1" UserProfile
    Customer "0..1" --> "1" User : account
    note for UserProfile "客户账号角色仅允许 customer / readonly\n有效权限再裁剪到客户安全域"
```

## 6. 服务工单状态机

```mermaid
stateDiagram-v2
    [*] --> draft
    draft --> pending : 提交
    pending --> approved : 审批通过
    pending --> rejected : 驳回
    approved --> provisioning : 开通中
    provisioning --> active : 交付完成
    active --> suspend_pending
    suspend_pending --> suspended
    suspended --> active : 恢复
    active --> terminate_pending
    terminate_pending --> terminated
    rejected --> [*]
    terminated --> [*]
```

生命周期阶段（`current_stage`）并行记录：下单 → 需求确认 → 测试 → 正式使用 → 变更 → 终止。

## 7. 服务实例状态机

```mermaid
stateDiagram-v2
    [*] --> provisioning
    provisioning --> active : activate
    provisioning --> error
    active --> suspended : suspend
    suspended --> active : resume
    active --> expired
    active --> terminated : terminate
    suspended --> terminated
    error --> provisioning : 重试
```

实例 `activate/suspend/terminate` 会同步 VPS / Edge / 带宽 / IP 的客户归属与状态。

## 8. 变更与计费时序

```mermaid
sequenceDiagram
    participant C as 客户门户
    participant P as portal API
    participant B as change_billing
    participant Pay as 支付网关
    participant X as change_executor

    C->>P: POST services/{id}/change_quote
    P->>B: quote_service_change
    B-->>C: 原月费 / 新月费 / 补差 / 升或降
    C->>P: POST change_request
    alt 升配需支付
        P->>B: 创建变更 + 预付账单
        P-->>C: checkout
        C->>Pay: 支付
        Pay->>P: webhook
        P->>X: 执行规格变更
    else 降配
        P->>B: 记录变更，下周期生效
    else 续期/终止
        P->>P: 创建审批工单
    end
```

## 9. 权限模型

```mermaid
flowchart LR
    subgraph catalog [权限目录]
      G[PermissionGroup 模块]
      PG[PermissionPage 页面]
      D[PermissionDefinition 权限码]
      G --> PG --> D
    end
    R[角色枚举]
    Grant[RolePermissionGrant]
    R --> Grant
    D --> Grant
    U[UserProfile.role]
    U --> Grant
    Eff[user_effective_permissions]
    U --> Eff
    Grant --> Eff
```

权限码层级（上级覆盖下级）：

1. 模块：`service.manage`
2. 页面：`service.change.manage`
3. 配置分类 / 配置项：`system.settings.billing.manage`

动作仅两种：`view`（只读）、`manage`（增删改执行）。

## 10. 管理端前端模块（路由）

```mermaid
flowchart TB
    Dash[数据概览]
    Cust[客户管理<br/>列表/详情/合同/账单/渠道]
    Res[资源管理<br/>POP/IP/隧道/VPS/Edge/带宽]
    Ops[运维<br/>主机/品质/流量/Zabbix/WebSSH]
    Svc[服务<br/>全景/工单/咨询/实例/变更]
    Prod[产品<br/>产品/模版]
    Sup[供应商<br/>列表/资源]
    Sys[系统<br/>参数/权限/用户/通知]
    Dash --- Cust
    Dash --- Res
    Dash --- Ops
    Dash --- Svc
    Dash --- Prod
    Dash --- Sup
    Dash --- Sys
```

路由守卫读取 `meta.permission`，与 `accounts.role.view` 等页面码匹配。

## 11. 门户前端模块

```mermaid
flowchart LR
    Pub[官网<br/>首页/产品/价格]
    Auth[登录/注册]
    Con[控制台]
    Pub --> Auth --> Con
    Con --> S[我的服务/变更]
    Con --> R[VPS/Edge]
    Con --> O[订单]
    Con --> I[咨询]
    Con --> B[账单]
    Con --> P[资料]
    Dock[在线客服 QQ/微信/TG]
    Pub --- Dock
    Con --- Dock
```

## 12. 实时通道

| WebSocket | 用途 | 鉴权 |
|-----------|------|------|
| `/ws/resource/vnc/{vps_id}/` | VPS noVNC | JWT 查询参数 |
| `/ws/resource/pop-ssh/{pop_id}/` | POP SSH | JWT |
| `/ws/operation/webssh/{type}/{id}/` | 运维 WebSSH | JWT；type=`pop\|vps\|monitoring_host\|custom` |

中间件：`resource_manager.ws_auth.JWTAuthMiddleware`。

## 13. 逻辑分层

```mermaid
flowchart TB
    subgraph access [接入层]
      A1[JWT / API Token]
      A2[IsAdmin / HasPermission / IsCustomer]
      A3[限流 public_inquiry / portal_monitoring]
    end
    subgraph domain [领域服务]
      D1[开通 provisioning]
      D2[变更报价 change_billing]
      D3[支付履约 payment_fulfillment]
      D4[产品计价 product_services]
      D5[节点下发 deploy_service]
      D6[IM 通知 im_services]
      D7[Zabbix 客户端]
    end
    subgraph persist [持久化]
      P1[(MySQL 业务表)]
    end
    access --> domain --> persist
```
