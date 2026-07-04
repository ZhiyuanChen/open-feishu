# OpenFeishu 示例

这些示例面向 SDK 使用者，均使用公开 API。凭证读取和网络调用发生在运行入口，模块导入可用于测试、检查和框架加载。运行前请先阅读对应目录或文件中的说明，确认所需权限、环境变量和真实调用范围。

| 示例 | 说明 |
| --- | --- |
| [`agent/`](agent/) | 可部署的飞书 Agent 模板。默认使用长连接接收消息，适合本地、内网和容器常驻部署；也可切换到 HTTP Webhook。 |
| [`oauth_login.py`](oauth_login.py) | 用户 OAuth 登录桥接示例：`authorize_url` -> `exchange_code` -> `user_info`，包含 CSRF `state` 校验与 HTML 转义。 |
| [`ai_stream_card.py`](ai_stream_card.py) | 以独立 `async main` 把 OpenAI / Anthropic 流式输出写入飞书 CardKit 卡片。 |

通用应用凭证从环境变量读取：

```bash
export FEISHU_APP_ID=cli_xxxxxxxx
export FEISHU_APP_SECRET=<app-secret>
```

凭证通过环境变量注入；示例输出限定为非密运行结果。

## Agent 模板

```bash
cd examples/agent
cp .env.example .env
pip install -r requirements.txt
set -a; source .env; set +a
python app.py
```

默认 `AGENT_BACKEND=ws`，适合本地或容器里直接通过飞书长连接收消息。容器部署：

```bash
cd examples/agent
cp .env.example .env
docker compose up --build
```

如需公网 Webhook，把 `.env` 中的 `AGENT_BACKEND` 改为 `http`，配置 `FEISHU_ENCRYPT_KEY` / `FEISHU_VERIFICATION_TOKEN`，并把飞书后台的事件 Request URL 指向 `https://<host>/feishu/event`。

更完整的 Agent 说明见仓库根目录的 `README.md` 和站点文档 `docs/docs/guides/agent.md`。

## OAuth 登录

```bash
pip install open-feishu uvicorn
export FEISHU_REDIRECT_URI=http://localhost:8000/callback
uvicorn examples.oauth_login:app --port 8000
# 打开 http://localhost:8000/login
```

在飞书应用后台登记完全一致的 Redirect URL，并授予 `contact:user.base:readonly` 与 `offline_access`。如需邮箱或手机号，额外申请对应的用户权限。

## 流式卡片

```bash
pip install 'open-feishu[openai]'
export OPENAI_API_KEY=<openai-api-key>
export RECEIVE_ID=<open-id-or-chat-id>
export RECEIVE_ID_TYPE=open_id
python examples/ai_stream_card.py
```

设置 `PROVIDER=anthropic` 可切换到 Anthropic。该示例会向 `RECEIVE_ID` 发送真实消息卡片，因此必须显式提供接收者。
