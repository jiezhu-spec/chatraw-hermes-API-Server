# Hermes API Server Front End

ChatRaw-Hermes API Server turns ChatRaw into a web front end for a Hermes API Server. ChatRaw owns the browser experience: chat list, composer, markdown rendering, collapsed thought process, plugin settings, tool activity display, and message persistence. Hermes owns the agent runtime behind its API Server: planning, model routing, skills, tools, approvals, memory, and session state.

This repository is intentionally API-Server-only. The default transport is Hermes `/v1/runs`; the CLI bridge implementation is not part of this repository.

## Default Layout

- ChatRaw web UI and backend: `http://127.0.0.1:51234/`
- Hermes API Server: `http://127.0.0.1:8642/v1`
- Docker Compose service: `chatraw-hermes-api-server`
- GitHub repository: `https://github.com/jiezhu-spec/chatraw-hermes-API-Server.git`

The Compose file uses host networking so the container can reach a Hermes API Server listening on the host loopback address.

## Quick Start

```bash
git clone https://github.com/jiezhu-spec/chatraw-hermes-API-Server.git
cd chatraw-hermes-API-Server
docker compose up -d --build
```

Open ChatRaw:

```text
http://127.0.0.1:51234/
```

For a remote host such as `10.10.99.99`, open:

```text
http://10.10.99.99:51234/
```

Configure the Hermes Router plugin:

- Base URL: `http://127.0.0.1:8642/v1` when ChatRaw and Hermes API Server run on the same host.
- Model: `hermes-agent`
- API endpoint: `Runs endpoint`
- API Server Key: the value configured for Hermes API Server.

For non-loopback Base URLs, add the exact URL to **Allowed remote base URLs**, review and confirm the warning, save settings, then click **Check**.

## Data Chain

```text
Browser ChatRaw UI
  -> POST /api/hermes/chat
  -> ChatRaw backend origin checks and saved Hermes settings
  -> Hermes API Server /v1/runs
  -> Hermes agent runtime, tools, skills, memory, MCP tools
  -> /v1/runs/{run_id}/events
  -> ChatRaw streaming assistant bubble and persisted message record
```

Responsibilities:

| Layer | Responsibility |
| --- | --- |
| ChatRaw frontend | Chat list, composer, markdown, collapsed thought process, tool activity UI, plugin settings. |
| ChatRaw backend | Origin checks, API key custody, remote URL allowlist, session id mapping, event normalization, message persistence. |
| Hermes API Server | `/v1/models`, `/v1/runs`, run event streaming, approvals, model/provider execution, skills, tools, memory, MCP cooperation. |

## MCP Cooperation

The main ChatRaw-to-Hermes transport is `/v1/runs`. MCP remains inside the Hermes cooperation layer:

1. **Hermes as an MCP Server**: start Hermes with `hermes mcp serve`; other MCP clients connect to Hermes.
2. **Hermes as an MCP Client**: add external MCP servers with `hermes mcp add`; Hermes imports those tools and may call them during a run.

ChatRaw does not need to become an MCP client for the main chat path. It preserves the Hermes run/session chain and renders the tool events emitted by Hermes, whether those tools are native Hermes tools, skills, or MCP-imported tools.

## Tool Activity

ChatRaw shows Hermes tool activity as compact, collapsible execution evidence:

- `Working...` while Hermes is planning or running tools.
- Rows for tools such as terminal, web search, file search, Python, or skill names.
- Started tools show an in-progress indicator.
- Completed tools show a success indicator.
- Tool details stay collapsed by default, with args and results available when expanded.
- The visible assistant answer remains the primary content.

## Runtime Behavior

In `runs` mode:

1. ChatRaw posts the user message to `/api/hermes/chat`.
2. The backend creates or resumes a Hermes session with `X-Hermes-Session-Id: chatraw-<chat_id>`.
3. The backend sends `Authorization: Bearer <API Server Key>` when a key is saved.
4. Hermes API Server creates a run and streams events.
5. ChatRaw normalizes message, thinking, tool, approval, error, and completion events.
6. ChatRaw streams answer text into the assistant bubble.
7. Tool events are shown in the tool activity panel and persisted in `messages.tool_calls`.

If Hermes sends both incremental deltas and later full-text snapshots, ChatRaw appends only the new suffix. This prevents repeated answer text when an API Server emits final snapshots after deltas.

## Timeouts And Long Jobs

`runs` mode keeps an HTTP/SSE connection open while Hermes emits events. The run event timeout is configurable:

```bash
HERMES_RUN_EVENT_TIMEOUT=1800
```

Related knobs are `HERMES_RUN_CREATE_TIMEOUT`, `HERMES_RUN_STOP_TIMEOUT`, `HERMES_RUN_CONNECT_TIMEOUT`, `HERMES_CHAT_TIMEOUT`, and `HERMES_HTTP_TIMEOUT`.

For long terminal work, prefer background execution plus log polling. Examples include `docker compose pull`, `docker compose up`, package installs, model downloads, large builds, and server/watch processes.

## Security Boundaries

- The browser never receives the Hermes API Server Key or Session Key in plaintext.
- Hermes route selection is host-limited: the plugin may only return `{ success: true, route: "hermes" }`.
- The host maps `hermes` to same-origin `/api/hermes/chat`.
- Hermes base URL must use `http` or `https`.
- Loopback hosts such as `localhost`, `127.0.0.1`, and `::1` are allowed by default.
- Non-loopback hosts are allowed only when the backend-normalized Base URL is listed in `allowedRemoteBaseUrls` and the saved risk-confirmation snapshot matches the current canonical list.
- Unicode domain names are normalized to punycode before allowlist and risk-confirmation comparison.
- Base URL paths must be empty or simple ASCII paths such as `/v1` or `/api/v1`; Unicode paths, percent escapes, dot segments, empty segments, and repeated slashes are rejected.
- URL credentials, query strings, and fragments are always rejected.
- Hermes API Server requests use `allow_redirects=False`; any 3xx response is blocked instead of followed.
- `/api/proxy/request` is not used for Hermes and continues to reject localhost and private network targets.

## Local Tests

Run these before opening or updating a pull request:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m py_compile backend/main.py
.venv/bin/python -m unittest backend.test_hermes_bridge
node --check backend/static/app.js
docker build -t chatraw-hermes-api-server:test .
```

Live service checks:

```bash
curl -fsS http://127.0.0.1:8642/v1/models -H "Authorization: Bearer <API_SERVER_KEY>"
curl -fsS http://127.0.0.1:51234/api/hermes/health -H "Referer: http://127.0.0.1:51234/"
ss -ltnp | grep -E ':(51234|8642)'
```

Compatibility prompts:

- `介绍下你的能力` should answer through the Hermes API Server runtime and show tool activity only when tools are used.
- `看下99服务器上的文件夹有哪些` should inspect the target host through Hermes tools and persist tool args/results.
- A time or PPT skill request should surface the selected tool or skill in ChatRaw tool activity.

## GitHub Workflow

Use `main` as the stable branch. Put each change behind an issue and pull request:

```bash
git clone https://github.com/jiezhu-spec/chatraw-hermes-API-Server.git
cd chatraw-hermes-API-Server
git checkout main
git pull origin main
git checkout -b codex/<short-change-name>
```

Before committing:

```bash
git status --short
git diff --stat
git add <only-files-that-belong-to-this-change>
git commit -m "fix: describe the change"
```

Push and open a pull request:

```bash
git push -u origin codex/<short-change-name>
gh pr create -R jiezhu-spec/chatraw-hermes-API-Server --base main --head codex/<short-change-name>
```

After the PR is open:

```bash
gh pr view <number> -R jiezhu-spec/chatraw-hermes-API-Server
gh run list -R jiezhu-spec/chatraw-hermes-API-Server --branch codex/<short-change-name>
```

Recommended management pattern:

- Keep `main` deployable.
- Create one issue for each meaningful change.
- Use PRs for all functional changes.
- Require CI and Docker build checks before merge.
- Deploy servers by pulling from GitHub, not by editing server files as the source of truth.

## Packaging And Release

Docker Compose build:

```bash
docker compose up -d --build
docker compose logs -f chatraw-hermes-api-server
```

GitHub Actions release packaging is defined in `.github/workflows/release.yml`. Pushing a `v*` tag builds and publishes:

- `ghcr.io/jiezhu-spec/chatraw-hermes-api-server:<version>`
- `ghcr.io/jiezhu-spec/chatraw-hermes-api-server:latest`

Example:

```bash
git tag v0.1.0
git push origin v0.1.0
docker pull ghcr.io/jiezhu-spec/chatraw-hermes-api-server:latest
docker compose up -d
```

## Troubleshooting

- **Hermes plugin is not enabled**: install the Hermes Router plugin and enable it in ChatRaw's plugin panel.
- **Missing Origin for Hermes API Server bridge**: call `/api/hermes/health` and `/api/hermes/chat` from the ChatRaw same-origin UI or include the expected local `Origin` / `Referer` during health checks.
- **Remote Hermes base URL requires risk confirmation**: add the URL to **Allowed remote base URLs**, review the warning, confirm it, and save settings.
- **Remote Hermes base URL risk confirmation is stale**: the allowed list changed after confirmation. Review and confirm the warning again, then save settings.
- **Hermes base URL must be listed in allowed remote base URLs**: the saved Base URL is remote but does not match any normalized URL in the allowed list.
- **Invalid Hermes base URL / Allowed remote Hermes base URL**: use `http` or `https`; remove credentials, query strings, fragments, and complex paths.
- **Hermes API error (401)**: the saved API Server Key is missing or does not match Hermes.
- **Hermes network error / timeout**: confirm Hermes API Server is running and listening on the configured host and port.
- **Error: session busy**: an earlier run is still active. Wait for it to finish or stop it from Hermes before submitting another prompt into the same session.
- **Hermes run event stream ended before completion**: Hermes closed the event stream without a terminal event. ChatRaw stops the run best-effort and does not save a partial assistant message as a completed answer.

# Hermes API Server 前端说明

ChatRaw-Hermes API Server 的定位是：用 ChatRaw 做 Hermes API Server 的网页前端。ChatRaw 负责前端体验、对话列表、消息保存、Markdown、插件设置和工具活动展示；Hermes API Server 负责 agent 推理、技能、工具、审批、记忆和 session 状态。

本仓库只做 API Server 模式。默认传输是 Hermes `/v1/runs`，不包含 CLI bridge 实现。

## 默认服务

- ChatRaw 页面和后端：`http://127.0.0.1:51234/`
- Hermes API Server：`http://127.0.0.1:8642/v1`
- Docker Compose 服务名：`chatraw-hermes-api-server`
- GitHub 仓库：`https://github.com/jiezhu-spec/chatraw-hermes-API-Server.git`

Compose 使用 host network，因此容器内可以访问宿主机上的 `127.0.0.1:8642`。

## 快速启动

```bash
git clone https://github.com/jiezhu-spec/chatraw-hermes-API-Server.git
cd chatraw-hermes-API-Server
docker compose up -d --build
```

打开：

```text
http://127.0.0.1:51234/
```

插件配置：

- Base URL：`http://127.0.0.1:8642/v1`
- Model：`hermes-agent`
- API 端点：`Runs endpoint`
- API Server Key：Hermes API Server 配置的 key

如果 Base URL 是远程地址，需要先填入 **Allowed remote base URLs**，阅读风险提示并确认，然后保存并点击 **检查**。

## 数据链

```text
浏览器 ChatRaw UI
  -> POST /api/hermes/chat
  -> ChatRaw 后端校验 Origin 和 Hermes 设置
  -> Hermes API Server /v1/runs
  -> Hermes agent runtime、工具、技能、记忆、MCP 工具
  -> /v1/runs/{run_id}/events
  -> ChatRaw 流式助手气泡和持久化消息
```

ChatRaw 不直接持有 Hermes 的运行时能力，只负责安全转发、事件归一化、前端渲染和消息保存。Hermes 工具、技能和 MCP 工具调用会通过 run event stream 回到 ChatRaw，并显示在 tool activity 中。

## 测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m py_compile backend/main.py
.venv/bin/python -m unittest backend.test_hermes_bridge
node --check backend/static/app.js
docker build -t chatraw-hermes-api-server:test .
```

## GitHub 标准流程

每个改动走 issue、分支、PR、Actions：

```bash
gh issue create -R jiezhu-spec/chatraw-hermes-API-Server --title "..." --body "..."
git checkout -b codex/<short-change-name>
git add <files>
git commit -m "..."
git push -u origin codex/<short-change-name>
gh pr create -R jiezhu-spec/chatraw-hermes-API-Server --base main --head codex/<short-change-name>
gh run list -R jiezhu-spec/chatraw-hermes-API-Server --branch codex/<short-change-name>
```
