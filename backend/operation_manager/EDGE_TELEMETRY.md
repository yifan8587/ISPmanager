# 边缘接口品质遥测

POP 节点将已下发隧道接口的 ping 质量样本写入 ISPmanager 中心库，客户门户据此展示真实的延迟、丢包、抖动和可用性数据。

## 1. 签发 POP 令牌

仅系统账号且具有运维管理权限的用户可操作：

```http
POST /api/operation/pop-telemetry-credentials/
Content-Type: application/json

{"pop_point": 12}
```

创建和轮换响应会额外包含一次性 `token`。中心仅保存其 SHA-256 摘要；后续列表、详情和日志都不会返回原始值。

```http
POST /api/operation/pop-telemetry-credentials/{id}/rotate/
```

轮换会立即使旧令牌失效并重新启用该 POP 的凭据。将新值保存到节点的受保护 secret/环境变量中；不要写入部署脚本、git、监控配置的备注或命令历史。

## 2. 节点上报

节点不使用用户 JWT，而是向以下公共接收路径提供专用头：

```http
POST /api/operation/edge-quality/ingest/
X-POP-Telemetry-Token: ${POP_TELEMETRY_TOKEN}
Content-Type: application/json
```

```json
{
  "samples": [
    {
      "source_sample_id": "node-boot-7:1700000000:gre42",
      "interface_name": "gre42",
      "target_address": "198.51.100.42",
      "sampled_at": "2026-08-19T09:30:00Z",
      "latency_ms": 18.4,
      "packet_loss_pct": 0,
      "jitter_ms": 1.6,
      "packets_sent": 5,
      "packets_recv": 5,
      "ok": true,
      "central_edge_id": 42
    }
  ]
}
```

- 每请求最多 500 条；`source_sample_id` 在同一 POP 内幂等。
- 中心按每个遥测令牌限流；节点应按批次重试，不要对单条样本高频请求。
- 中心只接受 `credential.pop_point` 下、`node_ifname` 与 `interface_name` 精确一致的 `EdgeAcceleration`。
- `central_edge_id` 和任何节点传入的 customer 信息都不会用于授权或写库归属；客户从中心 Edge 记录回填。
- 采样时间只能在过去 7 天至未来 5 分钟内。
- 响应包含 `accepted`、`duplicates`、`rejected` 计数，及 `accepted_ids`、`duplicate_ids`、
  `rejections[source_sample_id, reason]`，节点可据此安全推进部分成功批次的游标。

节点不应在样本 `raw`、请求体扩展字段或日志中携带令牌。中心的 `raw` 字段仅保存协议白名单遥测字段，也不会回显到客户门户。

## 3. 节点监控对象同步

隧道实际创建、重配成功或复用后，ISPmanager 会幂等同步节点侧对象：

- `MonitorTarget.name = isp-edge-quality-{edge.pk}`
- `source_interface = edge.node_ifname`
- `customer = edge.customer.customer_no`
- `telemetry_enabled = true`
- `central_edge_id = edge.pk`

探测地址按协议选择：

- GRE：`GREConfig.remote_ip`
- WireGuard：`WireguardConfig.peer_endpoint` 的主机部分（去掉端口和 IPv6 方括号）
- VXLAN：`VXLANConfig.remote_ip`

同时会创建或更新同名 `MonitorInterface`，但该对象不是 `MonitorTarget.source_interface` 的依赖。协议不支持或无法解析对端时，已有 `MonitorTarget` 会被停用，避免继续向旧 peer 采样。遥测同步异常会写入 `edge_tunnels.telemetry_errors`，不会回滚已经成功的隧道交付。

## 4. 客户门户状态

- 流量：仅取客户当前资源的可见 `MonitoringGraphBinding`，优先 `panel_slot=traffic`，从 Zabbix 实时查询；未绑定、无历史和 Zabbix 不可用会返回不同状态，绝不合成数据。
- 品质：从中心 `EdgeInterfaceQualitySample` 聚合；没有节点接口为 `not_configured`，没有样本为 `no_data`，最新样本超过 3 分钟为 `stale`。

如需撤销节点权限，先将对应 POP 凭据 `is_active` 设为 `false`；如需恢复，轮换令牌并更新节点 secret。

## 5. 数据保留与汇总

中心保留近期原始样本用于排障，同时维护小时/日汇总以支撑客户门户的长时间曲线。建议在
ISPmanager 的定时任务中每小时运行一次：

```bash
cd /opt/ISPmanager/backend
../venv/bin/python manage.py rollup_edge_quality_samples --retention-days 30
```

- 默认原始样本保留 30 天；已写入小时和日汇总后再删除更早的原始记录。
- 门户 7 天查询会优先使用小时汇总；尚未首次执行汇总时会安全回退到原始样本。
- 首次上线或需要核查汇总时，可使用
  `rollup_edge_quality_samples --full --no-prune` 全量重建汇总而不清理原始数据。
- 常规定时任务只重算最近 48 小时的开放桶，并在删除到期原始样本前补齐旧桶，避免每小时
  扫描完整保留期数据；可按需要通过 `--lookback-hours` 调整。
