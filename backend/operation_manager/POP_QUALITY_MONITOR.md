# POP 品质监控守护进程部署

POP 品质监控直接探测资源管理中 POP 的管理 IP（ICMP）和管理 URL（HTTP/HTTPS），不依赖 Zabbix。

## 前置条件

```bash
sudo apt-get install -y iputils-ping curl
cd /root/ISPmanager/backend
/root/ISPmanager/venv/bin/python manage.py migrate
```

首次启动前，可在“运维管理 → 监控管理 → POP 品质监控”点击“发现 POP”；守护进程默认也会自动为配置了管理 IP 或管理 URL 的 POP 创建监控对象。

手工验证单轮执行：

```bash
/root/ISPmanager/venv/bin/python manage.py run_pop_quality_monitor --once
```

默认策略为每 60 秒探测一次、丢包率达到 20%、连续 2 次时告警、15 分钟内最多重复提醒一次；这些参数可按 POP 覆盖。维护中的 POP 继续采样，但不会产生新的丢包告警。

## systemd

标准生产部署执行 `deploy/scripts/deploy.sh` 时，会自动安装并启用
`ispmanager-pop-quality.service`。日常查看状态或重启可使用：

```bash
cd /root/ISPmanager/deploy/scripts
bash manage-service.sh status
sudo bash manage-service.sh restart
bash manage-service.sh quality-logs
```

如需手工部署或独立安装服务，创建 `/etc/systemd/system/ispmanager-pop-quality.service`：

```ini
[Unit]
Description=ISPmanager POP Quality Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<deploy-user>
Group=<deploy-user>
WorkingDirectory=/root/ISPmanager/backend
ExecStart=/root/ISPmanager/venv/bin/python manage.py run_pop_quality_monitor --poll-seconds 5 --batch-size 20 --retention-days 90
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

将 `<deploy-user>` 改为实际部署账号后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ispmanager-pop-quality
sudo systemctl status ispmanager-pop-quality
sudo journalctl -u ispmanager-pop-quality -f
```

## supervisor（可选）

```ini
[program:ispmanager-pop-quality]
directory=/root/ISPmanager/backend
command=/root/ISPmanager/venv/bin/python manage.py run_pop_quality_monitor --poll-seconds 5 --batch-size 20 --retention-days 90
user=<deploy-user>
autostart=true
autorestart=true
startsecs=5
stopasgroup=true
killasgroup=true
stdout_logfile=/var/log/ispmanager/pop-quality.log
redirect_stderr=true
environment=PYTHONUNBUFFERED="1"
```

命令通过数据库租约领取到期任务，因此即使短时间内启动多个服务实例，同一个 POP 也不会被并发重复探测。采样默认保留 90 天，并由守护进程每日清理。
