# 变更记录 D2 — 客户技术工单（与服务工单分离）SLA 字段

**阶段**：D（P1 交付与客服）  
**步骤**：D2  
**日期**：2026-08-23  
**对应建议**：`05` 第 2.7 节「统一工单：技术故障 / 滥用投诉 / 财务 / 接入（与服务工单分离）」+ SLA

---

## 功能更新

1. 新增 **技术工单** `SupportTicket`，不挂在 `ServiceOrder` 上：
   - 分类：`technical` / `abuse` / `billing` / `access`
   - 优先级：`p1`–`p4`；状态：待处理 / 处理中 / 待客户回复 / 挂起 / 已解决 / 已关闭
   - 来源：门户 / 后台 / 官网咨询转入
2. **SLA**：创建时按优先级写入 `first_response_due` / `resolve_due`。默认小时：
   - P1 响应 1 / 解决 4
   - P2 4 / 24
   - P3 8 / 72
   - P4 24 / 168  
   可在系统参数 `support_sla_hours`（JSON）调整。首次**对外**客服回复写 `first_responded_at`；解决/关闭写时间戳。
3. 命令 `process_support_sla`：扫描开放工单，超时则打标并推送 IM 事件 `service_ticket`。默认不自动改派；`--apply-escalate` 才提升 `escalation_level`（最高 2）。`--dry-run` 只报告。
4. 建单、对外回复回写 CRM `ContactRecord`（在线沟通）。
5. 官网咨询可 `convert-to-ticket`（须已绑定客户），与现有 `convert-to-order` 独立，可同时存在。
6. 门户 `/api/portal/tickets/`：客户提交、查看、回复；**看不到内部备注**。

本步不做：知识库、公开状态页、IM 超时自动转工单（仅预留参数 `support_im_timeout_minutes`）。

---

## 变更文件

| 文件 | 变更 |
|------|------|
| `backend/service_manager/models.py` | `SupportTicket` / `SupportTicketMessage`；咨询跟进增加 `convert_ticket` |
| `backend/service_manager/migrations/0016_support_ticket_sla.py` | 迁移 |
| `backend/service_manager/support_ticket.py` | SLA、回写联系记录、逾期扫描 |
| `backend/service_manager/management/commands/process_support_sla.py` | 管理命令 |
| `backend/service_manager/views.py` / `urls.py` / `serializers.py` / `admin.py` | 管理端 CRUD、指派/回复/解决/关闭/升级；咨询转入 |
| `backend/portal/views.py` / `urls.py` / `serializers.py` | 门户工单与回复 |
| `backend/system_manager/default_settings.py` | SLA 参数与 IM 超时预留 |
| `backend/system_manager/models.py` / `notification_templates.py` / `0019` | 事件 `service_ticket` |
| `backend/accounts/role_permissions.py` | 页面 `service.ticket`；客户经理可管、销售可看 |
| `frontend` / `ISPweb` | 技术工单页；咨询「转技术工单」 |

---

## 验证

```bash
cd ISPmanager/backend
python manage.py migrate
python manage.py sync_permissions
python manage.py test service_manager.tests_support_tickets --keepdb
```

P1 工单的响应截止应约为创建后 1 小时。客服内部备注不写首次响应时间，也不出现在门户详情。门户用户看不到其他客户的工单。咨询转技术工单后再次转入应 400。将响应截止改到过去后：

```bash
python manage.py process_support_sla --dry-run
python manage.py process_support_sla
```

dry-run 不打标；正式运行后 `sla_response_breached=true` 并通知（若已配置规则）。

---

## 下一步（需确认）

**D3**：门户重装 / 快照等自助（后续独立立项）。

请回复：`确认 D2，继续 D3`。
