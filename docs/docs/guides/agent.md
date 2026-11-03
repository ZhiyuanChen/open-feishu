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
| `/feishu/card` | 独立卡片回调 URL，同步返回处理函数的 toast/card。 |
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

OAuth 授权与工具审批是独立步骤：取得用户授权不会自动批准写操作；要求审批的工具仍须经过确认。

事件中的用户 OAuth 凭证按租户隔离。旧版没有租户标识的 token 不会自动迁移到任何租户；升级后，用户需要在该租户中重新授权。自定义 token provider 需要实现 `as_user_for_tenant(tenant_key, user)` 和 `has_scopes_for_tenant(tenant_key, user, scopes)`；缺少这些接口时不会回退读取旧 token。旧版事件未携带租户时，单租户部署可以显式配置可信的 `feishu.tenant_key`；若事件携带的租户与配置值冲突，处理会被拒绝。既没有事件租户也没有可信配置时，Agent 不读取用户 token。授权回调必须从飞书返回的用户信息验证相同租户；缺失租户时拒绝保存凭证。

Agent 默认 OAuth state 有效期为一小时；直接构造 `OAuthStateSigner` 或自定义较短 `oauth.state_ttl_seconds` 时，授权链接可能先于待处理授权过期，需重新发起授权。

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

### 持久记忆

启用 `memory` bundle 后，Agent 只会获得两个工具：`recall_memory(query)` 和需要确认的
`remember_memory(scope, content)`。持久记忆不会在会话开始或每次模型调用前自动加入 prompt；模型只有在需要过去的
偏好或决定时，才应使用带关键词的检索工具。检索结果和普通工具结果一样，只在该次显式调用后进入会话。

```python
config["memory"] = {"enabled": True, "namespace": "support"}
config["toolkits"] = ["feishu.workplace", "memory"]
```

`user` scope 只对当前 Feishu 用户可见；`project` scope 由同一 `memory.namespace` 内的用户共享。保存由现有审批机制
确认后才落库。

内置 SQLite 存储会持久化同一用户的 `user_id`、`open_id` 和 `union_id` 关联，保持记忆在身份字段变化和重启后可检索。
自定义存储可实现 [feishu.agent.memory.MemoryIdentityStore][] 扩展，持久化同一用户的身份关联。
`recall_memory` 的查询最长为 200 个字符，每次检索数量为 1–50 条，超出范围会返回工具错误。

### Profiles

当不同聊天需要不同 prompt、模型、toolkit 或 memory namespace 时，显式装配一个 engine 之后再用
[feishu.agent.ProfiledAgent][] 路由。profile 不是用户身份：OAuth、审批和授权仍然按发起用户处理。每个 engine 的
`session_namespace` 必须等于 profile ID，使会话及后续卡片/OAuth 恢复始终回到创建它的 profile。

```python
from feishu.agent import AgentProfile, ProfiledAgent, ProfileRouter
from feishu.agent.registration import create_agent_dispatcher

router = ProfileRouter(
    profiles=(AgentProfile("default"), AgentProfile("oncall", chat_ids=("oc_oncall",))),
    default_profile="default",
)
runtime = ProfiledAgent(
    {
        "default": default_engine,  # AgentEngine(..., session_namespace="default")
        "oncall": oncall_engine,  # AgentEngine(..., session_namespace="oncall")
    },
    router,
)
dispatcher = create_agent_dispatcher(runtime)
```

## 状态与会话

`storage.path` 指向 Agent 的 SQLite 数据库，默认保存：

- 会话历史。
- 待审批工具调用。
- 工具执行结果。
- 用户 OAuth token。
- 已共享文件索引。
- 事件去重状态（当 `server.seen_store = "sqlite"` 时）。
- 已确认的 user/project memory（当 `memory.enabled = true` 时）。

常用会话配置：

| 配置 | 说明 |
| --- | --- |
| `session.max_messages` | 每个会话最多保留的消息数。`0` 表示不按条数裁剪。 |
| `session.summarize_threshold_tokens` | 超过阈值后触发历史摘要；`0` 表示关闭。 |
| `session.summarize_keep_recent` | 摘要后至少保留的最近消息数；必要时保留更多消息以保持完整工具调用与结果。 |
| `session.idle_session_timeout_seconds` | 会话空闲超过该秒数后清空普通历史；`0` 表示关闭。 |

`reply.stream = true` 时，Agent 会优先用流式卡片展示模型输出；如果正在执行工具，进度卡片会随工具状态更新。

### 并发和多 worker

同一个 `AgentEngine` 在私聊中会尝试用新消息取消正在处理的旧消息；群聊中的独立话题可以并发，同一话题的消息按顺序处理。会话锁和取消操作仅在该 engine 的进程内生效，不能跨 worker 协调。多个 worker 直接处理同一会话时，可能同时读取和更新会话历史；共享 SQLite 数据库或其他会话存储本身不提供跨进程的完整会话锁或取消信号。

如果为 HTTP 事件注入 `event_job_store` 并按会话设置 `event_partition_key`，持久收件箱会按分区顺序认领消息；同一会话的前一任务完成之前，后一任务不会运行。这个顺序保护不等于私聊中断：新消息在队列中等待时不会取消正在其他 worker 上执行的旧消息。卡片和 OAuth 回调如果使用独立路径，也需要由部署方与消息处理协调。

需要同一会话可靠串行的部署，应使用单 worker，或由部署方确保所有相关消息和回调按会话协调；不要把当前进程内锁当作分布式锁。使用下方的 SQLite 持久回调并显式启用 `interrupt_private=True` 时，SDK 可在同库的 worker 之间协作中断私聊；默认关闭，其他后端仍保持原有 FIFO 行为。

协作中断只针对飞书明确标记为 `p2p` 的用户消息。此前已入队的审批或 OAuth 回调保持顺序，不会被后来的消息越过；群聊话题行为不变。正在执行的工具或飞书请求不会被强制取消，而是在结果确认并保存后于安全点退出；同步工具的后台线程同样会等待结束。租约失联、消息发送或外部写入结果不明确时作业进入 `UNKNOWN` 并阻塞该会话，必须由可信运维人员核对后处理，不能自动重放。使用该选项必须设置 `unknown_ttl_seconds=None`，并确保所有相关 worker 和存储使用同一个 SQLite 文件；不承诺不具备等价原子能力的其他存储或跨主机 SQLite 部署。

### 定时任务

可选地启用持久调度器，让用户通过对话请求一次性、固定间隔或按本地时间每天／每周执行任务：

```python
config = {
    # ... feishu、model 等配置 ...
    "storage": {"path": ".agent/agent.db"},
    "scheduling": {"enabled": True, "poll_interval": 5},
}
```

启用后会注册创建、列出、暂停、恢复和删除任务的工具。创建或修改需由创建者确认；定时轮次使用独立会话，结果发往创建时的聊天。任务、审批和用户身份按租户隔离。间隔不得少于 60 秒；跳过已错过的执行，不在服务重启后批量补跑。

调度器需要持久的会话、审批和授权存储。SQLite 调度数据必须与会话存储使用同一个数据库文件，以便在同一事务内校验执行租约并写入会话。调度器随 Agent 的 `start()` / `aclose()` 启动和排空。创建者身份必须来自经验证的飞书事件；不能由模型提供用户或租户标识。

定时任务默认为只读。只有显式声明 `scheduled_effect="read"` 的工具可自动调用；未声明副作用的工具视为写操作。`confirm_writes` 策略下的每次写操作仍需创建者单独确认。确认或授权超时不会自动执行写操作。执行或消息投递结果不明确时，会保留状态供核对，不能假定已执行或未执行。

执行租约过期后，run 会冻结为 `unknown`，不自动重放。可信运维程序可以用 `ScheduledTaskStore.unknown_runs(tenant_key=...)` 查看，再在确认旧执行者已停止、核对外部副作用之后，调用 `reconcile_unknown(run_id, tenant_key=..., actor=..., decision=..., reason=..., now=...)`。`decision` 是 `confirmed_succeeded`、`confirmed_failed` 或 `abandoned`；操作会保存审计记录，不重新执行任务。宿主必须先自行鉴权运维人员，不能直接把该接口暴露给任意聊天用户。

目前应使用单 worker，或由宿主对同一任务的执行和回调提供可靠的跨 worker 协调。调度器不会自动重放执行结果未知的任务。每日／每周任务可从创建者的 OAuth 用户资料更新时区：资料须同时匹配已验证的租户和用户，查询失败或缺少这些字段时保留上次使用的时区；查询按用户缓存并限制频率。

### 持久化消息与回调

HTTP 部署可以选择 `SqliteDurableCallbacks`，把飞书消息、审批与 OAuth 续接放入同一个会话分区。启用时必须使用同一个 SQLite 数据库保存会话、审批、授权、用户 token 和事件收件箱；必须配置飞书事件 `encrypt_key` 或 `verification_token`。创建前请停止旧版写入方和 worker，并先处理完旧格式的未完成事件；初始化会拒绝混入旧格式的队列。此模式目前只支持 HTTP，不支持 WS 接收。

```python
from feishu.agent import Agent
from feishu.agent.durable_callbacks import SqliteDurableCallbacks
from feishu.agent.persistence import SqlitePendingApprovalStore, SqlitePendingAuthorizationStore
from feishu.auth import SqliteOAuthTokenStore, UserTokenProvider
from feishu.events.jobs import SqliteEventJobStore

# client 和 config 由宿主创建；config 应包括事件鉴权与 OAuth 公网回调。
path = ".agent/agent.db"
jobs = SqliteEventJobStore(path, unknown_ttl_seconds=None)
tokens = SqliteOAuthTokenStore(path)
approvals = SqlitePendingApprovalStore(path)
authorizations = SqlitePendingAuthorizationStore(path)
inbox = SqliteDurableCallbacks(jobs, tokens, authorizations, approvals=approvals)
agent = Agent(
    config, client=client, user_tokens=UserTokenProvider(client, tokens),
    approvals=approvals, authorizations=authorizations, durable_callbacks=inbox,
)
asgi_app = agent.asgi_app()
runner = inbox.runner(agent, worker_id="unique-worker-id")
```

如果需要上述跨 worker 私聊中断，将 `interrupt_private=True` 传给 `SqliteDurableCallbacks`。该开关默认关闭，启用时要求事件存储的 `unknown_ttl_seconds=None`；旧版 worker 和写入方必须先全部停止，避免与新协议混用。

宿主还须在同一事件循环中持续调用 `await runner.run_once()`；无可运行工作时可短暂等待。worker ID 在并发实例间必须唯一，注入的 client 和各存储由宿主关闭。事件成功入队只表示持久接收，不能证明消息或写操作已经完成。`UNKNOWN` 需要先核对外部结果，再由有权的操作人员处理；不要自动重放。

OAuth 回调在兑换一次性 code 前持久记录接收状态；验证租户和用户后，在一个事务中保存用户 token 与授权续接任务。远程兑换和本地数据库不可能组成一个原子事务：如果进程在兑换后、提交前退出，同一授权请求不会自动再次兑换，用户需要重新发起授权。收件箱不保存 OAuth code、access token、事件验证 token 或卡片回调 token。

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
| `memory` | 持久记忆开关与项目 namespace；通过 `memory` bundle 显式检索和确认写入。 |
| `scheduling` | 可选定时任务；`enabled`、SQLite `path` 和 `poll_interval`。 |
| `bundle` | 内置工具 bundle 的本地化和摘要限制。 |
| `toolkits` | 要启用的工具 bundle 名称；传空列表时，运行时工具来自自定义 registry。 |

## 何时使用底层入口

如果你已经有自己的事件系统、模型调度或持久化层，可以直接装配 [feishu.agent.loop.AgentEngine][]，再用 [feishu.agent.registration.register_agent][] 挂到事件分发器。`Agent(config)` 适合“我想直接运行一个 Feishu agent 服务”的场景；`AgentEngine` 适合框架级集成。
