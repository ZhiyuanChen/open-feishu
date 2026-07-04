# OpenFeishu Agent 模板

这是一个可直接部署的飞书 Agent 模板。默认使用飞书长连接接收事件，适合本地、内网和容器常驻部署；生产环境也可以切换到 HTTP Webhook。

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
4. 默认 `AGENT_BACKEND=ws` 时，应用通过长连接收事件；切换到 `AGENT_BACKEND=http` 时，需要把事件 Request URL 配置为 `https://<host>/feishu/event`。

## 配置

```bash
cp .env.example .env
```

默认长连接模式必须填写：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`

`FEISHU_ENCRYPT_KEY` 和 `FEISHU_VERIFICATION_TOKEN` 只在 HTTP Webhook 模式必需，但模板会把它们保留在 `.env.example` 中，方便显式切换接入方式。

其他常用变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AGENT_BACKEND` | `ws` | 接入方式，支持 `ws` 或 `http`。 |
| `AGENT_DB_PATH` | `/data/agent/agent.db` | SQLite 状态库路径。 |
| `AGENT_TIMEZONE` | `Asia/Shanghai` | 默认时区。 |
| `AGENT_SYSTEM_PROMPT` | 中文简洁助手提示词 | 系统提示词。 |
| `HOST` | `0.0.0.0` | HTTP Webhook 监听地址。 |
| `PORT` | `5654` | HTTP Webhook 监听端口。 |

## 本地运行

```bash
pip install -r requirements.txt
set -a; source .env; set +a
python app.py
```

## Docker 运行

```bash
cp .env.example .env
docker compose up --build
```

默认数据库路径是 `/data/agent/agent.db`，`compose.yml` 会把宿主机的 `./data` 挂载到容器内。删除该目录会清空会话、审批、用户授权和事件去重状态。

## 扩展示例

模板里的 `get_time` 只演示自定义工具注册：

```python
registry = ToolRegistry()


@registry.register(...)
def get_time(...):
    ...
```

如果要启用内置办公工具，把 `app.py` 中的 `toolkits` 从 `[]` 改为需要的 bundle 名称，例如 `["feishu.workplace"]`。用户态工具还需要配置 `oauth.public_url`，并通过 HTTP 后端或产品层路由承接 OAuth 回调 `/oauth/callback`。
