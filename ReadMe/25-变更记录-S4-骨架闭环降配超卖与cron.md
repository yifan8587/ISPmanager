# 变更记录 S4 — 骨架闭环：降配、超卖与 cron

**阶段**：已有骨架真跑（依据 `21`）  
**步骤**：S4  
**日期**：2026-08-23  
**对应**：`21` B-P0-4 / B-P0-7，§6 定时任务，§7 第二批 1–3  
**前置**：S1、S2、S3 已确认

---

## 功能更新

1. **B-P0-7 降配执行器**：`process_scheduled_changes` 扫描 `scheduled_at` 已到期且已审批的变更，落库规格与月费，并把关联工单标为已开通。未来预约不执行；`--dry-run` 只报告。
2. **升配后可选工单**：规格落库后默认开一张「变更待节点下发」技术工单（系统参数 `change_followup_ticket`，可关）。不调 PVE resize，与既有骨架边界一致。
3. **B-P0-4 超卖规则统一**：开通、变更交付、容量恢复与预占共用 `capacity_allows()`：POP 打开 `allow_oversell` 时容量不足仍继续；容量校验异常不允许靠超卖硬开。
4. **deploy cron**：`ispmanager-maintenance.timer` 每 15 分钟跑履约 / 预占释放 / 过期支付 / 退款补扫 / 降配执行；`ispmanager-daily-jobs.timer` 每日 01:15 跑流量出账 / 逾期账单 / SLA / 品质汇总。自助重装命令仍不挂 timer（无 `--apply` 空跑）。

未做（非本步）：节点真实 resize、国内通道原路退、到期默认停机回收、邮件 SMTP、余额充值。

---

## 验证

```bash
cd ISPmanager/backend
/root/ISPmanager/venv/bin/python manage.py test \
  service_manager.tests_change_scheduler \
  service_manager.tests_change_billing \
  service_manager.tests_capacity_reservation \
  service_manager.tests.ServiceRequestFlowTests.test_capacity_check_error_waits_for_manual_delivery --keepdb
```

---

## 下一步

S4 已确认。见 `26-变更记录-S5-邮件通知闭环.md`。
