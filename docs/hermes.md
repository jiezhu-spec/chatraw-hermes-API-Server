# Hermes CLI Front End

ChatRaw-Hermes turns ChatRaw into a web front end for the Hermes CLI agent. ChatRaw owns the browser experience: chat list, composer, markdown rendering, collapsed thought process, plugin settings, and visible tool activity. Hermes owns the agent runtime: planning, skills, terminal/process/search tools, approvals, memory, and session state.

The preferred integration mode is `runs`. ChatRaw does not call Hermes from browser plugins directly. The browser calls same-origin ChatRaw APIs, the backend validates the saved Hermes settings, and the backend talks to the Hermes CLI bridge.

## Default Layout

Current default service layout:

- ChatRaw-Hermes web UI and backend: `http://127.0.0.1:51234/`
- Hermes CLI bridge: `http://127.0.0.1:51113/v1`
- Docker Compose service: `chatraw-hermes`
- GitHub repository: `https://github.com/jiezhu-spec/ChatRaw-Hermes.git`

Port `51111` is not used by the current Compose layout. If an older ChatRaw or Hermes service is still listening on `51111`, stop it before validating the current deployment.

## Quick Start

```bash
git clone https://github.com/jiezhu-spec/ChatRaw-Hermes.git
cd ChatRaw-Hermes
docker compose up -d --build
```

Open ChatRaw at:

```text
http://127.0.0.1:51234/
```

For a remote host such as `10.10.99.99`, use:

```text
http://10.10.99.99:51234/
```

Configure the Hermes Router plugin with:

- Base URL: `http://127.0.0.1:51113/v1` when ChatRaw and the bridge run on the same host.
- Base URL: `http://10.10.99.99:51113/v1` when the browser or another host should reach the bridge on `99`.
- Model: `hermes-agent`
- API Mode: `runs`

For non-loopback Base URLs, add the exact URL to **Allowed remote base URLs**, review and confirm the warning, save settings, then click **Check**.

## Timeout And Long Jobs

`runs` mode keeps an HTTP/SSE connection open while Hermes emits events. The run event timeout is configurable:

```bash
HERMES_RUN_EVENT_TIMEOUT=1800
```

If `HERMES_RUN_EVENT_TIMEOUT` is not set, ChatRaw falls back to `HERMES_BRIDGE_TIMEOUT` when present, then to `1800` seconds. Related knobs are `HERMES_RUN_CREATE_TIMEOUT`, `HERMES_RUN_STOP_TIMEOUT`, `HERMES_RUN_CONNECT_TIMEOUT`, `HERMES_CHAT_TIMEOUT`, and `HERMES_HTTP_TIMEOUT`.

For long terminal work, prefer background execution plus log polling. Examples include `docker compose pull`, `docker compose up`, `npm install`, `pip install`, model downloads, large builds, and server/watch processes. The bridge prompt tells Hermes to start these in the background, redirect output to a clear log file, return the PID/job id and log path, then poll with bounded commands such as `ps`, `tail -20`, `curl` health checks, or `docker ps`.

## Data Chain

The runtime chain is:

```text
Browser ChatRaw UI
  -> POST /api/hermes/chat
  -> Hermes Router backend config and origin checks
  -> Hermes CLI bridge /v1/runs
  -> python -m tui_gateway.entry
  -> Hermes CLI session, tools, skills, memory, state.db
  -> /v1/runs/{run_id}/events
  -> ChatRaw streaming assistant bubble and persisted message record
```

The main responsibilities are:

| Layer | Responsibility |
| --- | --- |
| ChatRaw frontend | Chat list, composer, markdown, collapsed thought process, tool activity UI, plugin settings. |
| ChatRaw backend | Origin checks, Hermes settings, remote URL allowlist, session id mapping, message persistence. |
| Hermes CLI bridge | Long-lived gateway, `/v1/runs`, non-interactive prompt wrapper, event normalization, tool result sync. |
| Hermes CLI / TUI gateway | Planning, skills, terminal/search/process tools, approvals, memory, `state.db`. |

## MCP Cooperation

ChatRaw-Hermes uses `/v1/runs` as the main chat transport between ChatRaw and Hermes. MCP is still an important cooperation layer inside the Hermes ecosystem, and there are two supported directions:

1. **Hermes as an MCP Server**: start Hermes with `hermes mcp serve`. Other MCP-capable tools can connect to Hermes and use the capabilities that Hermes exposes.
2. **Hermes as an MCP Client**: add external MCP servers with `hermes mcp add`. Hermes imports those servers' tools and can call them during the CLI agent run. ChatRaw then receives those tool activities through the Hermes run event stream and shows them in tool activity.

This means ChatRaw does not need to become an MCP client itself for the main chat path. ChatRaw only needs to preserve the Hermes run/session chain and render the tool events that Hermes emits, whether those tools are native Hermes tools, skills, or MCP-imported tools.

## Runs Mode Behavior

In `runs` mode:

1. ChatRaw posts the user message to `/api/hermes/chat`.
2. The backend creates or resumes a Hermes session with `X-Hermes-Session-Id: chatraw-<chat_id>`.
3. The bridge submits a non-interactive prompt to `tui_gateway.entry`.
4. Hermes emits text, reasoning, tool, approval, and completion events.
5. ChatRaw streams answer text into the assistant bubble.
6. Tool events are shown in the tool activity panel and persisted in `messages.tool_calls`.
7. On completion, the bridge reads the latest Hermes turn from `state.db` and backfills tool args, results, stdout, and stderr where Hermes only streamed partial progress earlier.

Current behavior that matters for compatibility:

- Capability prompts such as `介绍下你的能力` should answer from the local Hermes runtime and tool context. The bridge prompt tells Hermes not to use `web_search` unless the user explicitly asks for current external information.
- Tool `start`, `progress`, and `complete` events with the same `tool_id` are merged before persistence.
- Approval requests are auto-approved once by the bridge for non-interactive ChatRaw use, but they are not hidden. ChatRaw records a visible `approval` tool activity with the label `ChatRaw bridge auto-approved this Hermes request once.`
- Thinking content duplicated from the visible answer is stripped before saving.
- ChatRaw does not store Hermes private chain-of-thought. It only stores provider-supplied thinking after cleanup.

## Tool Activity UI

ChatRaw should show Hermes tool activity as a compact, collapsible block before or near the assistant answer. The UI should make the current phase clear:

- `Working...` while Hermes is planning or running tools.
- Tool rows such as `terminal`, `web_search`, `search_files`, `python`, or skill names.
- Started tools show an in-progress indicator.
- Completed tools show a success indicator.
- Approval events show as visible bridge approval activity.
- Tool details can stay collapsed by default, with args/results available when expanded.

This keeps the conversation readable while preserving the execution evidence needed for debugging.

## Security Boundaries

- Hermes route selection is host-limited: the plugin may only return `{ success: true, route: "hermes" }`.
- The host maps `hermes` to the same-origin `/api/hermes/chat` endpoint.
- The browser never receives the Hermes API key or Session Key in plaintext.
- Hermes base URL must use `http` or `https`.
- Loopback hosts such as `localhost`, `127.0.0.1`, and `::1` are allowed by default.
- Non-loopback hosts are allowed only when the backend-normalized Base URL is listed in `allowedRemoteBaseUrls` and the saved risk-confirmation snapshot matches the current canonical list.
- Unicode domain names are normalized to punycode before allowlist and risk-confirmation comparison.
- Base URL paths must be empty or simple ASCII paths such as `/v1` or `/api/v1`; Unicode paths, percent escapes, dot segments, empty segments, and repeated slashes are rejected.
- URL credentials, query strings, and fragments are always rejected.
- Hermes bridge requests use `allow_redirects=False`; any 3xx response is blocked instead of followed.
- `/api/hermes/*` is not a plugin metadata or static resource path and should not be exempted from normal API rate limiting.
- `/api/proxy/request` is not used for Hermes and continues to reject localhost and private network targets.

## Local Tests

Run these before opening or updating a pull request:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m py_compile bridge/hermes_chatraw_bridge.py backend/main.py
.venv/bin/python -m unittest backend.test_hermes_bridge
node --check backend/static/app.js
docker build -t chatraw-hermes:test .
```

Live service checks:

```bash
curl -fsS http://127.0.0.1:51113/health
curl -fsS http://127.0.0.1:51234/api/hermes/health -H "Referer: http://127.0.0.1:51234/"
ss -ltnp | grep -E ':(51111|51113|51234)'
```

Compatibility prompts:

- `介绍下你的能力` should use the Hermes CLI runtime context and should not call `web_search`.
- `看下99服务器上的文件夹有哪些` should inspect the RM01 host filesystem through Hermes tools and persist tool args/results.
- `请调用终端执行 python3 -c "print('APPROVAL_VISIBLE_OK')"` should show both `terminal` and visible `approval` tool activity.

## GitHub Workflow

Use `main` as the stable branch and put each change on a feature branch:

```bash
git clone https://github.com/jiezhu-spec/ChatRaw-Hermes.git
cd ChatRaw-Hermes
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
gh pr create -R jiezhu-spec/ChatRaw-Hermes --base main --head codex/<short-change-name>
```

After the PR is open:

```bash
gh pr view <number> -R jiezhu-spec/ChatRaw-Hermes
gh run list -R jiezhu-spec/ChatRaw-Hermes --branch codex/<short-change-name>
```

The recommended management pattern is:

- Keep `main` deployable.
- Use PRs for all functional changes.
- Require tests and Docker build checks before merge.
- Deploy servers by pulling from GitHub, not by editing server files as the source of truth.
- Keep unrelated local changes in separate commits or branches.

## Packaging And Release

Docker Compose build:

```bash
docker compose up -d --build
docker compose logs -f chatraw-hermes
```

GitHub Actions release packaging is defined in `.github/workflows/release.yml`. Pushing a `v*` tag builds and publishes:

- `ghcr.io/jiezhu-spec/chatraw-hermes:<version>`
- `ghcr.io/jiezhu-spec/chatraw-hermes:latest`

Example:

```bash
git tag v0.1.0
git push origin v0.1.0
docker pull ghcr.io/jiezhu-spec/chatraw-hermes:latest
docker compose up -d
```

## Troubleshooting

- **Hermes plugin is not enabled**: install the Hermes Router plugin and enable it in ChatRaw's plugin panel.
- **Missing Origin for Hermes bridge**: call `/api/hermes/health` and `/api/hermes/chat` from the ChatRaw same-origin UI or include the expected local `Origin` / `Referer` during health checks.
- **Remote Hermes base URL requires risk confirmation**: add the URL to **Allowed remote base URLs**, review the warning, confirm it, and save settings.
- **Remote Hermes base URL risk confirmation is stale**: the allowed list changed after confirmation. Review and confirm the warning again, then save settings.
- **Hermes base URL must be listed in allowed remote base URLs**: the saved Base URL is remote but does not match any normalized URL in the allowed list.
- **Invalid Hermes base URL / Allowed remote Hermes base URL**: use `http` or `https`; remove credentials, query strings, fragments, and complex paths.
- **Hermes API error (401)**: the saved API key is missing or does not match a bridge that requires one.
- **Hermes network error / timeout**: confirm the bridge is running and listening on the configured host and port.
- **Hermes approval appears in tool activity**: this is expected for terminal or privileged actions. The bridge auto-approves once for non-interactive ChatRaw use and records the event.
- **Hermes run event stream ended before completion**: Hermes closed the event stream without a terminal event. ChatRaw stops the run best-effort and does not save a partial assistant message as a completed answer.

# Hermes CLI 前端说明

ChatRaw-Hermes 的核心定位是：用 ChatRaw 做 Hermes CLI 的网页前端。ChatRaw 负责前端体验、对话列表、消息保存、Markdown、插件设置和工具活动展示；Hermes CLI 负责真正的 agent 推理、技能、工具、审批、记忆和 session 状态。

当前推荐使用 `runs` 模式。浏览器不会直接调用 Hermes；浏览器只调用 ChatRaw 同源接口，由 ChatRaw 后端完成配置校验、远程地址放行和 Hermes bridge 调用。

## 默认服务

- ChatRaw-Hermes 页面和后端：`http://127.0.0.1:51234/`
- Hermes CLI bridge：`http://127.0.0.1:51113/v1`
- Docker Compose 服务名：`chatraw-hermes`
- GitHub 仓库：`https://github.com/jiezhu-spec/ChatRaw-Hermes.git`

当前 Compose 不使用 `51111`。如果旧服务还占着 `51111`，应先停止旧服务再验收当前版本。

## 克隆和启动

```bash
git clone https://github.com/jiezhu-spec/ChatRaw-Hermes.git
cd ChatRaw-Hermes
docker compose up -d --build
```

本机打开：

```text
http://127.0.0.1:51234/
```

99 服务器打开：

```text
http://10.10.99.99:51234/
```

Hermes Router 插件推荐配置：

- Base URL：同机部署用 `http://127.0.0.1:51113/v1`
- Base URL：从其他机器访问 99 用 `http://10.10.99.99:51113/v1`
- Model：`hermes-agent`
- API Mode：`runs`

如果 Base URL 是非 loopback 地址，需要写入 **允许的远程 Base URL**，确认风险，保存后再点 **检查**。

## 超时和长任务

`runs` 模式会在 Hermes 输出事件期间保持 HTTP/SSE 连接。run event 超时可配置：

```bash
HERMES_RUN_EVENT_TIMEOUT=1800
```

如果没有设置 `HERMES_RUN_EVENT_TIMEOUT`，ChatRaw 会兼容读取旧的 `HERMES_BRIDGE_TIMEOUT`，再退回默认 `1800` 秒。相关配置还有 `HERMES_RUN_CREATE_TIMEOUT`、`HERMES_RUN_STOP_TIMEOUT`、`HERMES_RUN_CONNECT_TIMEOUT`、`HERMES_CHAT_TIMEOUT` 和 `HERMES_HTTP_TIMEOUT`。

长时间终端任务应走后台执行加日志轮询。典型任务包括 `docker compose pull`、`docker compose up`、`npm install`、`pip install`、模型下载、大型构建、server/watch 进程。bridge prompt 会要求 Hermes 后台启动任务、把输出重定向到明确的日志文件、返回 PID/job id 和日志路径，再用 `ps`、`tail -20`、`curl` 健康检查或 `docker ps` 等短命令轮询进度。

## 数据链路

```text
浏览器 ChatRaw UI
  -> POST /api/hermes/chat
  -> ChatRaw 后端 Hermes Router 配置和 Origin 校验
  -> Hermes CLI bridge /v1/runs
  -> python -m tui_gateway.entry
  -> Hermes CLI session、tools、skills、memory、state.db
  -> /v1/runs/{run_id}/events
  -> ChatRaw 流式显示 assistant，并保存消息和 tool_calls
```

关键分工：

| 层级 | 职责 |
| --- | --- |
| ChatRaw 前端 | 对话列表、输入框、Markdown、折叠思考过程、tool activity、插件设置。 |
| ChatRaw 后端 | Origin 校验、Hermes 配置、远程 URL 放行、session id 映射、消息持久化。 |
| Hermes CLI bridge | 长驻 gateway、`/v1/runs`、非交互 prompt 包装、事件归一化、工具结果补齐。 |
| Hermes CLI / TUI gateway | 规划、技能、终端/搜索/进程工具、审批、记忆、`state.db`。 |

## MCP 合作方式

ChatRaw-Hermes 使用 `/v1/runs` 作为 ChatRaw 和 Hermes 之间的主聊天通道。MCP 仍然是 Hermes 生态里的重要工具协作层，方向分两类：

1. **Hermes 作为 MCP Server**：通过 `hermes mcp serve` 启动。其他支持 MCP 的工具可以连接到 Hermes，使用 Hermes 暴露出来的能力。
2. **Hermes 作为 MCP Client**：通过 `hermes mcp add` 添加外部 MCP Server。Hermes 会把这些服务器的工具引入自己的 CLI agent 运行环境，在执行任务时调用。ChatRaw 再通过 Hermes run event stream 收到这些工具活动，并在 tool activity 中显示。

这意味着 ChatRaw 的主链路不需要自己变成 MCP Client。ChatRaw 需要做的是保持 Hermes run/session 链路稳定，并正确展示 Hermes 发出的工具事件；这些工具可以是 Hermes 原生工具、skill，也可以是 MCP 引入的外部工具。

## runs 模式如何工作

1. ChatRaw 把用户消息发到 `/api/hermes/chat`。
2. 后端用 `X-Hermes-Session-Id: chatraw-<chat_id>` 创建或恢复 Hermes session。
3. bridge 把请求提交给 `tui_gateway.entry`。
4. Hermes 输出文本、reasoning、工具、审批和完成事件。
5. ChatRaw 把文本流式写入 assistant 气泡。
6. 工具事件显示在 tool activity，并保存到 `messages.tool_calls`。
7. 完成后 bridge 读取 Hermes `state.db` 中最新用户轮次，补齐工具参数、结果、stdout、stderr。

当前已明确的行为：

- `介绍下你的能力` 这类能力说明问题，应从 Hermes CLI 本地运行环境和工具上下文回答；bridge prompt 会要求 Hermes 不要在没有明确要求外部实时信息时调用 `web_search`。
- 同一个 `tool_id` 的 start/progress/complete 会合并后保存。
- 审批请求会由 bridge 为非交互 ChatRaw 场景自动批准一次，但不会静默隐藏；ChatRaw 会记录一条 `approval` 工具活动。
- 与可见回答重复的 thinking 会在保存前清理。
- ChatRaw 不保存 Hermes 私有 chain-of-thought，只保存清理后的 provider-supplied thinking。

## Tool Activity 前端要求

ChatRaw 需要像 Hermes Client 一样显示工具活动，让用户知道当前处于什么阶段：

- Hermes 正在规划或执行时显示 `Working...`。
- 工具行显示 `terminal`、`web_search`、`search_files`、`python` 或 skill 名称。
- 执行中显示进度状态。
- 完成后显示成功状态。
- 自动审批也要作为 `approval` 活动显示。
- 参数和结果默认收起，需要时可展开。

这样对话内容默认保持干净，但排查问题时仍能看到完整执行证据。

## 本地测试

提交 PR 前执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m py_compile bridge/hermes_chatraw_bridge.py backend/main.py
.venv/bin/python -m unittest backend.test_hermes_bridge
node --check backend/static/app.js
docker build -t chatraw-hermes:test .
```

服务检查：

```bash
curl -fsS http://127.0.0.1:51113/health
curl -fsS http://127.0.0.1:51234/api/hermes/health -H "Referer: http://127.0.0.1:51234/"
ss -ltnp | grep -E ':(51111|51113|51234)'
```

兼容性问题：

- `介绍下你的能力`：不应调用 `web_search`，应按 Hermes CLI 本地能力回答。
- `看下99服务器上的文件夹有哪些`：应通过 Hermes 工具查看 RM01 主机文件系统，并保存工具参数和结果。
- `请调用终端执行 python3 -c "print('APPROVAL_VISIBLE_OK')"`：应同时看到 `terminal` 和可见的 `approval` 活动。

## GitHub 标准流程

以 `main` 作为稳定分支，每个功能使用独立分支：

```bash
git clone https://github.com/jiezhu-spec/ChatRaw-Hermes.git
cd ChatRaw-Hermes
git checkout main
git pull origin main
git checkout -b codex/<short-change-name>
```

提交前确认只包含本次变更：

```bash
git status --short
git diff --stat
git add <only-files-that-belong-to-this-change>
git commit -m "fix: describe the change"
```

推送并创建 PR：

```bash
git push -u origin codex/<short-change-name>
gh pr create -R jiezhu-spec/ChatRaw-Hermes --base main --head codex/<short-change-name>
```

PR 打开后查看：

```bash
gh pr view <number> -R jiezhu-spec/ChatRaw-Hermes
gh run list -R jiezhu-spec/ChatRaw-Hermes --branch codex/<short-change-name>
```

建议项目管理方式：

- `main` 始终保持可部署。
- 功能变更走 PR。
- 合并前必须通过测试和 Docker build。
- 服务器通过 GitHub 拉取部署，不把服务器手改文件当成源码。
- 不相关改动分开提交或分支管理。

## 打包发布

Docker Compose 本地打包：

```bash
docker compose up -d --build
docker compose logs -f chatraw-hermes
```

`.github/workflows/release.yml` 负责 GitHub Actions 发布。推送 `v*` tag 后会构建并发布：

- `ghcr.io/jiezhu-spec/chatraw-hermes:<version>`
- `ghcr.io/jiezhu-spec/chatraw-hermes:latest`

示例：

```bash
git tag v0.1.0
git push origin v0.1.0
docker pull ghcr.io/jiezhu-spec/chatraw-hermes:latest
docker compose up -d
```

## 常见问题

- **Hermes plugin is not enabled**：安装并启用 Hermes Router 插件。
- **Missing Origin for Hermes bridge**：从 ChatRaw 同源页面调用 `/api/hermes/health` 或 `/api/hermes/chat`；命令行检查时带上预期的 `Origin` 或 `Referer`。
- **Remote Hermes base URL requires risk confirmation**：把地址加入 **允许的远程 Base URL**，阅读并确认风险，然后保存。
- **Remote Hermes base URL risk confirmation is stale**：允许列表确认后发生变化，需要重新确认并保存。
- **Hermes base URL must be listed in allowed remote base URLs**：保存的远程 Base URL 没有命中规范化后的允许列表。
- **Invalid Hermes base URL / Allowed remote Hermes base URL**：使用 `http` 或 `https`，移除 URL 凭据、query、fragment 和复杂 path。
- **Hermes API error (401)**：保存的 API key 缺失，或 bridge 需要的 key 不一致。
- **Hermes network error / timeout**：确认 bridge 正在运行，并监听配置的 host 和 port。
- **Hermes approval appears in tool activity**：这是终端或高权限动作的预期表现。bridge 会为非交互 ChatRaw 自动批准一次，并记录可见活动。
- **Hermes run event stream ended before completion**：Hermes 在终态事件前关闭 events stream。ChatRaw 会 best-effort stop run，不把 partial assistant message 当作完整回答保存。
