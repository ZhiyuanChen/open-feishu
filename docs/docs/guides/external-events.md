# 外部事件和主动通知

外部服务可以使用 `ExternalEventIntegration` 将事件持久化到收件箱，并由 `EventJobRunner` 异步处理。HTTP `202` 表示已经接收，不表示处理完成或消息已经送达。此入口与飞书自身的 `/feishu/event` 相互独立。

客户端使用网关服务密钥发起 `POST /events/external`：

```json
{
  "event_id": "event-001",
  "event_type": "job.failed",
  "data": {
    "event_id": "job-42",
    "revision": 2,
    "title": "作业失败",
    "status": "failed",
    "summary": "作业 42 已失败。",
    "target": {"receive_id": "oc_example", "receive_id_type": "chat_id"}
  }
}
```

顶层 `event_id` 是**每次事件**的标识，同一个服务重试时必须保持不变；相同标识携带不同内容返回 409。通知的 `data.event_id` 是跨状态变化保持稳定的**卡片**标识；使用该字段时还须提供非负整数 `data.revision`，由事件源保证同一卡片的新状态有更大的版本号。旧版本和内容相同的重复版本不会覆盖新卡片；同一版本对应不同内容属于冲突，任务会失败并等待处理。给多个接收人投递时需要每次使用不同的顶层事件 ID；卡片版本按服务、卡片标识和接收人隔离。无需更新的单次通知可以省略 `data.event_id` 和 `data.revision`。

```python
from feishu.events import SqliteEventJobStore
from feishu.gateway import GatewayConfig, SqliteEventMessageStore, create_gateway
from feishu.integrations import ExternalEventIntegration, NotificationEventHandler

# 示例：实际密钥从部署方的密钥管理器读取，不要写入源码。
config = GatewayConfig(
    app_id="cli_example",
    app_secret="<app secret>",
    service_keys={"<service key>": "scheduler"},
    service_capabilities={"scheduler": frozenset({"/events/external"})},
)
job_store = SqliteEventJobStore("/var/lib/feishu/external-jobs.db", unknown_ttl_seconds=None)
message_store = SqliteEventMessageStore("/var/lib/feishu/message-mapping.db")
# feishu_client 由应用创建并管理生命周期。
handler = NotificationEventHandler(
    feishu_client,
    message_store,
    allowed_targets={"scheduler": {("chat_id", "oc_example")}},
)
app = create_gateway(
    config,
    client=feishu_client,
    integrations=(ExternalEventIntegration(
        job_store, authorize=handler.authorize, handler=handler, worker_id="unique-worker-id"
    ),),
)
```

示例配置 `handler` 和 `worker_id` 后，网关会随应用生命周期启动和关闭 worker；停止时等待当前任务，超过关闭期限后取消并按照副作用状态记录为可重试或 `UNKNOWN`。部署多个 worker 时，每个 worker 应使用不同的 `worker_id`，共享同一个支持并发的持久存储。事件收件箱、卡片映射与注入客户端由宿主管理并关闭，分别调用 `await job_store.close()` 和 `message_store.close()`。

如需独立 worker，可不设置 `handler` 和 `worker_id`，自行创建 `EventJobRunner(job_store, handler, worker_id=..., payload_decoder=ExternalEvent.from_payload)` 并运行它；网关只负责接收事件。

## 身份和恢复

- 服务身份来自 Bearer 密钥，而非请求体；`authorize=None` 不限制事件中的通知目标，通知场景应像示例一样配置 `handler.authorize`。应通过 `service_capabilities` 限制各服务可访问的路由。示例的 `allowed_targets` 是明确的服务到接收人白名单，入队和投递时均会检查。
- 同一服务的事件按接收顺序串行，避免同一来源的卡片并发更新。对于同一事件 ID 的重复接收，只有队列保留该记录期间能够去重；SQLite 存储默认的已完成记录保留时间为七天。
- SQLite 存储首次打开旧数据库时会迁移接收顺序。升级前应停止旧版本的所有写入方和 worker，再启动新版本；新旧版本混跑不能保证顺序，旧版本写入的事件也不会自动获得接收序号。
- 首次外部写操作前会标记副作用。标记前失败按存储和 runner 的策略重试；标记后失败或任务中断进入 `UNKNOWN`，需要先核对飞书实际结果，再由运维人员决定是否调用 `requeue_unknown`。不能承诺外部消息严格只发一次。
- 版本号由事件源提供，不能仅凭接收顺序推断事件发生顺序；事件源必须为同一作业的每次状态变化提供单调递增的版本。卡片映射存储须持久支持 `get_revision` / `set_revision`，内置 SQLite 存储满足此合同。消息发送成功但保存版本前发生中断时，必须先核对并恢复 `UNKNOWN` 事件，不得删除该记录后继续处理该来源。
- 异常结束或 `UNKNOWN` 会阻塞该来源的后续事件，其他来源可以继续。SQLite 默认在 30 天后清理 `UNKNOWN`，这会解除阻塞并删除待核对记录；示例设置 `unknown_ttl_seconds=None` 保留这些记录，直到人工处理。监控任务状态并提供人工处理流程，是宿主部署的一部分。
- 通知映射记录只保存卡片与消息的对应关系，不能取代持久收件箱，也不构成跨进程的完整 get/send/set 锁。外部事件 worker 的同来源分区顺序负责串行化本示例中的投递；不要将相同卡片键交由此队列之外的并发调用修改。
