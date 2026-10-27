# WebSSH / WebSocket 部署说明

POP 点部署使用浏览器 WebSSH（xterm.js + WebSocket），后端基于 **Django Channels**。
因此运行后端时必须使用 **ASGI** 服务，普通 WSGI（gunicorn 默认 sync worker）无法处理
`ws://.../ws/resource/pop-ssh/<id>/` 连接。

## 1. 开发环境

已将 `daphne` 注册到 `INSTALLED_APPS` 顶部，`runserver` 会自动以 ASGI 启动，
原样支持 WebSocket：

```bash
source ../venv/bin/activate
python manage.py runserver 0.0.0.0:8000
```

前端 `vite.config.js` 已增加 `/ws` 代理（`ws: true`），无需额外配置。

## 2. 生产环境

用 uvicorn worker 启动 gunicorn，使其同时处理 HTTP 与 WebSocket：

```bash
gunicorn backend.asgi:application \
  -k uvicorn.workers.UvicornWorker \
  --workers 4 \
  --bind 0.0.0.0:8000 \
  --timeout 600
```

或直接用 daphne：

```bash
daphne -b 0.0.0.0 -p 8000 backend.asgi:application
```

## 3. nginx 反向代理（关键：WebSocket 升级头）

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    server_name _;

    location / {
        root /opt/ispmanager/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 600s;
    }

    # WebSSH 终端 WebSocket —— 必须带 Upgrade / Connection 头
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 3600s;   # 终端长连接，避免被截断
    }
}
```

## 4. 鉴权说明

浏览器 WebSocket 无法自定义请求头，故 access token 通过 query string 传入：

```
ws://<host>/ws/resource/pop-ssh/<pop_id>/?token=<JWT access token>
```

后端 `resource_manager/ws_auth.py` 中间件校验 token 并注入 `scope['user']`，
未认证连接会被直接关闭（code 4401）。

## 5. 部署包上传目录

上传的 `*.tar.gz` 部署包默认存放在 `backend/media/deploy_packages/`，
可用环境变量覆盖：

| 变量 | 默认 | 说明 |
|---|---|---|
| `POP_DEPLOY_UPLOAD_DIR` | `<BASE_DIR>/media/deploy_packages` | 上传包存放目录 |
| `POP_DEPLOY_MAX_UPLOAD` | `524288000`（500MB） | 单包大小上限 |
| `POP_DEPLOY_REMOTE_DIR` | `/tmp/pop_deploy` | 节点上解压 / 部署工作目录 |

## 6. Channel Layer（多进程）

未设置 Redis 时使用 `InMemoryChannelLayer`，仅本进程有效。

生产多 gunicorn worker 请安装 Redis，并设置其一：

| 变量 | 说明 |
|------|------|
| `CHANNEL_REDIS_URL` | 优先，如 `redis://127.0.0.1:6379/0` |
| `REDIS_URL` | 同上，通用后备 |
| `CHANNEL_LAYER_BACKEND` | `redis` 强制 Redis；`memory` 强制内存 |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` | 无 URL 时拼连接串 |

依赖：`channels-redis`（见 `requirements.txt`）。
