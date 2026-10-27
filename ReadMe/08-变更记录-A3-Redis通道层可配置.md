# 变更记录 A3 — Redis Channel Layer 可配置

**阶段**：A（现有逻辑与安全）  
**步骤**：A3  
**日期**：2026-08-23  
**对应建议**：`05` 第 3.6 节；内存通道层在多 gunicorn worker 下无法跨进程

---

## 功能更新

1. `CHANNEL_LAYERS` 按环境变量组装：未配置 Redis 时仍为 `InMemoryChannelLayer`（本地零配置）。
2. 设置 `CHANNEL_REDIS_URL` 或 `REDIS_URL` 后使用 `channels_redis`，WebSSH / VNC 可跨 worker 共享通道。
3. `CHANNEL_LAYER_BACKEND=memory` 可强制回退内存；`=redis` 可在无 URL 时按 `REDIS_HOST` 等拼连接串。
4. 部署脚本把上述变量写入 `/etc/ispmanager.env`。
5. **当前这台机器未安装 Redis**，运行时仍走内存，行为与 A3 前一致。

---

## 环境变量

| 变量 | 说明 |
|------|------|
| `CHANNEL_REDIS_URL` | 优先，如 `redis://127.0.0.1:6379/0` |
| `REDIS_URL` | 通用后备 |
| `CHANNEL_LAYER_BACKEND` | `redis` / `memory` |
| `REDIS_HOST` `REDIS_PORT` `REDIS_PASSWORD` `REDIS_DB` | 无 URL 时拼连接串 |
| `CHANNEL_REDIS_PREFIX` | Redis key 前缀，默认 `ispmanager` |

通道消息用 `DJANGO_SECRET_KEY` 做对称加密（`symmetric_encryption_keys`）。

启用示例：

```bash
sudo apt-get install -y redis-server
# deploy/.env
CHANNEL_LAYER_BACKEND=redis
CHANNEL_REDIS_URL=redis://127.0.0.1:6379/0
# 然后重新 deploy.sh，或把同样变量写入 /etc/ispmanager.env 后重启 ispmanager
```

---

## 变更文件

| 文件 | 变更 |
|------|------|
| `backend/backend/channel_config.py` | 通道层组装 |
| `backend/backend/settings.py` | `CHANNEL_LAYERS = build_channel_layers(...)` |
| `backend/requirements.txt` | `channels-redis==4.3.0` |
| `backend/system_manager/tests_channel_config.py` | 内存 / Redis 切换单测 |
| `deploy/scripts/deploy.sh` | 运行时环境写入 Redis 相关变量 |
| `deploy/.env.example` / `deploy/.env` | 配置说明 |
| `deploy/README.md`、`backend/WEBSOCKET_DEPLOY.md` | 多 worker 说明 |
| `ReadMe/00` `01` `03` `04` `05` | 文档同步 |

---

## 验证

```bash
cd ISPmanager/backend
python manage.py test system_manager.tests_channel_config --keepdb
```

未设 Redis 时 `CHANNEL_LAYERS` 后端应为 `channels.layers.InMemoryChannelLayer`。

---

## 下一步（需确认）

**A4**：客服「对外联系」与「对内告警」拆分。

请回复：`确认 A3，继续 A4`。
