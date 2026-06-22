---
authors:
  - Zhiyuan Chen
date: 2022-05-04
---

# OpenFeishu

OpenFeishu 是一个飞书开放平台的 Python SDK，提供了飞书开放平台的接口封装，方便开发者使用飞书开放平台的接口。

## 使用

### 快速开始

`FeishuClient` 是一个异步客户端，统一管理应用凭证、令牌的自动获取与缓存，以及带自动重试的 HTTP 传输。推荐通过 `async with` 使用，以便自动释放底层连接。消息接口以接收者为第一个参数（其 ID 类型会自动推断），消息内容为第二个参数；`client.im.send` 还会根据内容的形态自动推断消息类型（`msg_type`）。

```python
import asyncio

from feishu import FeishuClient


async def main():
    async with FeishuClient("cli_xxx", "app_secret") as client:
        await client.im.send("oc_xxx", "hello, world!")


asyncio.run(main())
```

应用凭证也可以通过环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 提供，此时可省略构造参数：`async with FeishuClient() as client: ...`。

### 命名空间

各业务能力按资源划分到不同命名空间下，每个命名空间提供「裸动词」式的 CRUD 方法（如 `create` / `get` / `update` / `delete` / `list`）。常用入口：

| 命名空间 | 说明 |
| --- | --- |
| `client.im` | 即时消息：发送、回复、编辑、撤回、转发消息 |
| `client.contact.users` / `client.contact.departments` | 通讯录：用户与部门 |
| `client.bitable.tables` | 多维表格：数据表、字段与记录 |
| `client.calendar.events` | 日历：日程与参与人 |
| `client.approval.instances` | 审批：审批实例与任务 |
| `client.drive.files` | 云空间：文件的上传、下载、复制与删除 |

此外还有 `client.docx`（新版文档）、`client.sheets`（电子表格）、`client.wiki`（知识库）、`client.board`（画板）、`client.vc`（视频会议）、`client.task`（任务）、`client.oauth`（用户身份 OAuth）、`client.cards`（卡片构建器）等命名空间。

```python
async with FeishuClient("cli_xxx", "app_secret") as client:
    user = await client.contact.users.get("ou_xxx", user_id_type="open_id")
    records = await client.bitable.records.list("app_token", "tbl_xxx")
```

### 接收事件

要接收飞书推送的事件（如「接收消息」），先用 `EventDispatcher` 按事件类型注册异步处理函数，Webhook 接收器与长连接两种接入方式共用同一套处理函数。

```python
from feishu.events import EventDispatcher

dispatcher = EventDispatcher()


@dispatcher.on("im.message.receive_v1")
async def on_message(event):
    print(event.event_id)
```

**方式一：Webhook 接收器。** 当应用有公网回调地址时，用 `create_event_app` 生成一个可独立运行的 Starlette 应用，默认带签名新鲜度（防重放）与去重保护，以任意 ASGI 服务器（如 `uvicorn`）运行即可：

```python
from feishu.events import create_event_app

app = create_event_app(dispatcher, encrypt_key="ek_secret")
# uvicorn module:app
```

**方式二：长连接（WebSocket）。** 当应用没有公网回调地址时，用 `feishu.ws.WsClient` 主动与飞书建立一条持久 WebSocket 连接（对标 Slack 的 Socket Mode），事件经该连接推送：

```python
import asyncio

from feishu.ws import WsClient

ws = WsClient("cli_xxx", "app_secret", dispatcher)
asyncio.run(ws.start())
```

长连接依赖可选的 `websockets` 包，可通过 `pip install open-feishu[ws]` 安装。

## 安装

从 PyPI 安装最新的稳定版本：

```shell
pip install open-feishu
```

从源代码安装最新版本：

```shell
pip install git+https://github.com/ZhiyuanChen/open-feishu.git
```

## 许可证

`SPDX-License-Identifier: AGPL-3.0-or-later`
