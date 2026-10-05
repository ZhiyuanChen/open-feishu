# OpenFeishu Agent 模板

这是一个可直接部署的飞书 Agent 模板。Docker Compose 使用 HTTP Webhook，提供 `/health` 和 `/ready` 探针；本地运行也可以切换到飞书长连接。

## 文件

| 文件 | 用途 |
| --- | --- |
| `app.py` | 构造 `Agent(config(), registry=registry)`，并注册一个 `get_time` 示例工具。 |
| `.env.example` | 模板支持的环境变量清单。 |
| `requirements.txt` | 安装 OpenFeishu、OpenAI 适配器、长连接和 HTTP Webhook 运行依赖。 |
| `Dockerfile` / `compose.yml` | 最小容器部署配置，SQLite 数据挂载到 `./data`。 |

## 飞书后台配置

1. 创建自建应用，启用机器人能力。
2. 授予读取与回复消息所需权限，例如 `im:message`；群聊 @ 消息还需要 `im:message.group_at_msg`。
3. 订阅 `im.message.receive_v1`。如果启用工具审批，还需要让卡片回调事件能到达同一 Agent。
4. Docker Compose 使用 HTTP Webhook，把事件 Request URL 配置为 `https://<host>/feishu/event`；卡片回调配置为 `https://<host>/feishu/card`。

## 配置

```bash
cp .env.example .env
```

必须填写：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`

HTTP Webhook 模式应按飞书后台配置 `FEISHU_ENCRYPT_KEY` 和 `FEISHU_VERIFICATION_TOKEN`。如果启用用户态工具，还要设置公开的 `AGENT_PUBLIC_URL` 和专用 `AGENT_OAUTH_STATE_SECRET`，并把 OAuth 回调地址登记为 `https://<host>/oauth/callback`。

其他常用变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AGENT_BACKEND` | `http` | 接入方式，支持 `ws` 或 `http`。Compose 固定使用 HTTP，供健康探针访问。 |
| `AGENT_DB_PATH` | `/data/agent/agent.db` | SQLite 状态库路径。 |
| `AGENT_TIMEZONE` | `Asia/Shanghai` | 默认时区。 |
| `AGENT_SYSTEM_PROMPT` | 中文简洁助手提示词 | 系统提示词。 |
| `AGENT_SHUTDOWN_TIMEOUT_SECONDS` | `30` | Agent 排空任务和关闭自有资源的时限。 |
| `HOST` | `0.0.0.0` | HTTP Webhook 监听地址。 |
| `PORT` | `5654` | HTTP Webhook 监听端口。 |

## 本地运行

```bash
cd examples/agent
pip install -r requirements.txt
set -a; source .env; set +a
python app.py
```

## Docker 运行

```bash
cd examples/agent
cp .env.example .env
docker compose up --build
```

Compose 从当前仓库源码构建 OpenFeishu，并把数据库保存到 `examples/agent/data`。`/health` 检查进程存活，`/ready` 在 ASGI 启动完成且尚未进入退出流程时返回成功。容器收到停止信号后会先停止接收请求、排空 Agent 任务，再关闭它创建的连接和 SQLite 存储；`stop_grace_period` 为这两个阶段留出时间。

Kubernetes 参考配置在 `k8s.yaml`：先构建并推送 `open-feishu-agent:local` 镜像，再创建名为 `open-feishu-agent-secrets` 的 Secret（至少包含飞书 App ID/Secret、事件加密与校验值、模型 API Key），然后应用 YAML。把 Service 接到你自己的 TLS Ingress，设置公开 HTTPS `AGENT_PUBLIC_URL`，并在飞书后台配置 `/feishu/event` 和 `/feishu/card`。

存活探针访问 `/health`，启动与就绪探针访问 `/ready`。示例使用 SQLite 和 ReadWriteOnce 持久卷，Deployment 保持单副本并用 `Recreate` 避免两个 Pod 同时写同一数据库；退出宽限期为 70 秒，对应 Uvicorn 和 Agent 各自最多 30 秒的收尾窗口。扩副本前需要共享会话与任务协调存储。

## 扩展示例

模板里的 `get_time` 只演示自定义工具注册：

```python
registry = ToolRegistry()


@registry.register(...)
def get_time(...):
    ...
```

如果要启用内置办公工具，把 `app.py` 中的 `toolkits` 从 `[]` 改为需要的 bundle 名称，例如 `["feishu.workplace"]`。Agent 会在创建客户端和数据库之前校验已知配置；自定义 backend、registry 和 engine 仍可通过 Python 注入。嵌入现有 ASGI 应用时，使用 `agent.asgi_app()` 复用同一生命周期；直接管理对象时可使用 `await agent.start()` 和 `await agent.aclose()`。
