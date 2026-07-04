# Agent

`Agent(config)` 是 OpenFeishu 面向机器人产品的高层入口。它负责把飞书事件接入、LLM 后端、工具注册、会话存储、审批卡片、用户 OAuth、附件解析和进度回复装配成一个可运行服务；需要自定义事件系统、模型调度或持久化层时，可直接装配底层的 [feishu.agent.loop.AgentEngine][]。

最小可部署模板在仓库的 `examples/agent` 目录。模板默认使用长连接接收飞书事件，适合本地、内网和容器常驻部署；切换到 HTTP 后端后，可把同一个 Agent 暴露为 Webhook 服务。

## 安装

```bash
pip install "open-feishu[openai,ws,gateway]"
```

可选依赖含义：

| extra | 用途 |
| --- | --- |
| `openai` | 使用 OpenAI-compatible Chat Completions 后端。 |
| `ws` | 使用飞书长连接接收事件。 |
| `gateway` | 使用 `uvicorn` 运行 HTTP Webhook。 |
| `attachments` | 解析用户发来的文档、图片、表格等附件。 |

## 最小示例

```python
from feishu.agent import Agent, ToolRegistry

registry = ToolRegistry()


@registry.register(
    input_schema={
        "type": "object",
        "properties": {
            "tz": {"type": "string", "description": "时区，支持 'utc' 或 'local'。"},
        },
        "required": [],
        "additionalProperties": False,
    },
    description="返回当前时间。",
)
def get_time(tz: str = "utc") -> str:
    ...


config = {
    "feishu": {
        "app_id": "cli_xxx",
        "app_secret": "app_secret",
        "region": "feishu",
    },
    "model": {
        "model": "gpt-4o-mini",
        "api_key": "sk_xxx",
        "base_url": "https://api.openai.com/v1",
    },
    "storage": {"path": ".agent/agent.db"},
    "toolkits": [],
    "timezone": "Asia/Shanghai",
    "system": "你是一个简洁的飞书助手。必要时使用工具。",
}

Agent(config, registry=registry).run(backend="ws")
```

[feishu.agent.Agent][] 接收已经加载好的 mapping。产品层可以用自己的配置系统读取 YAML、TOML、`.env`、环境变量或密钥管理服务，再把结果传入 `Agent(config)`。

## 接入方式

### 长连接

长连接适合本地开发、内网部署和容器常驻服务：

```python
Agent(config, registry=registry).run(backend="ws")
```

长连接底层使用 [feishu.ws.WsClient][]。它会主动连接飞书并接收事件，消息事件和卡片回调会分发给同一个 [feishu.events.EventDispatcher][]。用户 OAuth 回调由 HTTP 路由 `/oauth/callback` 承接；启用用户态工具时，建议使用 HTTP 后端，或在产品层额外提供同等回调路由。

### HTTP Webhook

HTTP 后端适合已经有公网入口的服务：

```python
Agent(config, registry=registry).run(backend="http")
```

HTTP 后端会暴露：

| 路径 | 用途 |
| --- | --- |
| `/feishu/event` | 飞书事件 Request URL，处理消息事件与卡片回调。 |
| `/health` | 健康检查。 |
| `/oauth/callback` | 用户 OAuth 回调；配置 `oauth.public_url` 后启用。 |

Webhook 模式需要在 `config["feishu"]` 中提供 `encrypt_key` 与 `verification_token`，并在飞书后台把事件 Request URL 配置为 `https://<host>/feishu/event`。

## 工具

自定义工具通过 [feishu.agent.ToolRegistry][] 注册：

```python
registry = ToolRegistry()


@registry.register(input_schema={...}, description="查询内部系统状态。")
async def query_status(service: str) -> dict:
    ...
```

注册表传给 `Agent(config, registry=registry)` 后，模型即可在对话中调用这些工具。写操作如果需要人工确认，应使用底层 [feishu.agent.tools.Tool][] 的审批能力，或复用内置工具 bundle 中已经标记审批语义的工具。

内置办公工具通过 `toolkits` 启用：

```python
config["toolkits"] = ["feishu.workplace"]
```

`feishu.workplace` 会注册日历、任务、审批、文档、邮件、会议室等工具。需要用户身份执行的工具会通过 OAuth 申请用户授权；生产部署应配置：

```python
config["oauth"] = {
    "public_url": "https://agent.example.com",
    "state_secret": "...",
}
```

bundle 构建逻辑见 [feishu.agent.bundles.build_tool_registry][] 与 [feishu.agent.bundles.BundleContext][]。

## 状态与会话

`storage.path` 指向 Agent 的 SQLite 数据库，默认保存：

- 会话历史。
- 待审批工具调用。
- 工具执行结果。
- 用户 OAuth token。
- 已共享文件索引。
- 事件去重状态（当 `server.seen_store = "sqlite"` 时）。

常用会话配置：

| 配置 | 说明 |
| --- | --- |
| `session.max_messages` | 每个会话最多保留的消息数。`0` 表示不按条数裁剪。 |
| `session.summarize_threshold_tokens` | 超过阈值后触发历史摘要；`0` 表示关闭。 |
| `session.summarize_keep_recent` | 摘要后保留的最近消息数。 |
| `session.idle_session_timeout_seconds` | 会话空闲超过该秒数后清空普通历史；`0` 表示关闭。 |

`reply.stream = true` 时，Agent 会优先用流式卡片展示模型输出；如果正在执行工具，进度卡片会随工具状态更新。

## 配置速查

| 配置段 | 说明 |
| --- | --- |
| `feishu` | 应用凭证、区域、Webhook 校验参数。 |
| `model` | OpenAI-compatible 后端的 `model`、`api_key`、`base_url`，以及可选 thinking 参数。 |
| `fast_model` | 进度总结、附件描述和历史摘要可用的轻量模型。 |
| `storage` | SQLite 数据库与审计日志路径。 |
| `server` | HTTP host、port、事件路径、健康检查路径、事件去重存储。 |
| `ws` | 长连接卡片回调同步 ack 超时等参数。 |
| `oauth` | 用户 OAuth 回调地址、state 密钥和授权恢复 TTL。 |
| `shared_files` | 用户共享文件的缓存大小与 TTL。 |
| `bundle` | 内置工具 bundle 的本地化和摘要限制。 |
| `toolkits` | 要启用的工具 bundle 名称；传空列表时，运行时工具来自自定义 registry。 |

## 何时使用底层入口

如果你已经有自己的事件系统、模型调度或持久化层，可以直接装配 [feishu.agent.loop.AgentEngine][]，再用 [feishu.agent.registration.register_agent][] 挂到事件分发器。`Agent(config)` 适合“我想直接运行一个 Feishu agent 服务”的场景；`AgentEngine` 适合框架级集成。
