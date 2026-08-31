# Creator Pass H5 最小部署

这套部署只启动 Creator Pass 网页和 API，不启动 SMTP、IMAP、Milvus、AI 或批次发送。

## 服务器环境变量

项目根目录的 `.env` 至少需要：

```dotenv
TARGET_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/creator_projects
FORM_PUBLIC_BASE_URL=https://coojoy.cn/creator
FORM_TOKEN_SECRET=至少32个字符且本地与服务器完全一致
FORM_TOKEN_TTL_DAYS=90
```

`.env` 不会被复制进镜像，而是在容器启动时读取。

## 启动

在项目根目录执行：

```bash
docker compose -f deploy/h5/compose.yaml up -d --build
docker compose -f deploy/h5/compose.yaml ps
curl http://127.0.0.1:8001/health
```

预期健康检查返回：

```json
{"status":"ok"}
```

## 查看日志和重启

```bash
docker compose -f deploy/h5/compose.yaml logs --tail=100 creator-pass
docker compose -f deploy/h5/compose.yaml restart creator-pass
```

宝塔 Nginx 将 `https://coojoy.cn/creator/` 反向代理到：

```text
http://127.0.0.1:8001
```

正式投放前必须使用真实签名链接提交一次，并在旧 PostgreSQL 中按 `creator_id` 核对写入结果。
