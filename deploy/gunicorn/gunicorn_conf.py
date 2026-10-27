"""
gunicorn 生产配置 — 以 uvicorn worker 运行 ASGI 应用（支持 WebSocket）

启动：
  gunicorn backend.asgi:application -c deploy/gunicorn/gunicorn_conf.py

关键点：
  - 使用 UvicornWorker 才能同时处理 HTTP 与 WebSocket（noVNC / WebSSH）
  - 配置项从环境变量读取，交由 systemd 注入
"""
import multiprocessing
import os

bind = os.environ.get('APP_BIND', '127.0.0.1:8000')

# ASGI worker（支持 WebSocket）
worker_class = 'uvicorn.workers.UvicornWorker'

# worker 数量：默认 CPU*2+1，可用 GUNICORN_WORKERS 覆盖
_default_workers = multiprocessing.cpu_count() * 2 + 1
workers = int(os.environ.get('GUNICORN_WORKERS', _default_workers))

# WebSocket 长连接需较大超时
timeout = int(os.environ.get('GUNICORN_TIMEOUT', 120))
graceful_timeout = 30
keepalive = 5

# 上传大文件（POP 部署包）时避免过早重启
limit_request_line = 0
limit_request_field_size = 0

# 日志输出到 stdout/stderr，由 systemd/journald 收集
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOGLEVEL', 'info')

proc_name = 'ispmanager'

# 信任 nginx 传入的 X-Forwarded-*（本机反代）
forwarded_allow_ips = os.environ.get('GUNICORN_FORWARDED_ALLOW_IPS', '127.0.0.1,::1')
