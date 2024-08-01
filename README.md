---
authors:
  - Zhiyuan Chen
date: 2022-05-04
---

# OpenFeishu

OpenFeishu 是一个飞书开放平台的 Python SDK，提供了飞书开放平台的接口封装，方便开发者使用飞书开放平台的接口。

## 使用

### 发送消息

```python
from feishu import variables, send_message

variables.app_id = 'app_id'
variables.app_secret = 'app_secret'

send_message('hello, world!', 'chat_id')
```

### 调用 ChatGPT 回复用户消息

```python
from feishu import variables, send_message, get_gpt_completions

variables.app_id = 'app_id'
variables.app_secret = 'app_secret'
variables.openai_key = 'openai_key'

content = get_gpt_completions([dict(role='user', content='Hi, How are you?')])
send_message(content, "message_id")
```

### 飞书机器人

```python
from feishu.robot import han

variables.app_id = 'app_id'
variables.app_secret = 'app_secret'
variables.openai_key = 'openai_key'

def handle_event(request: NestedDict) -> dict:
    if not isinstance(request, NestedDict):
        request = NestedDict(request)
    event = request.get("event")
    event["id"] = request.get("header", {}).get("event_id")
    event["type"] = request.get("header", {}).get("event_type")
    if event["type"] == "im.message.receive_v1":
        return handle_chat(env, request)
```

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
