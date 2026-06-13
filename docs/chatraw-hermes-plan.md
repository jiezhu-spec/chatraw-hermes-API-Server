# ChatRaw-Hermes API Server 项目计划

## 目标

把 ChatRaw-Hermes 独立成 API Server 专用仓库：`jiezhu-spec/chatraw-hermes-API-Server`。

这个仓库只负责 ChatRaw 到 Hermes API Server 的安全路由和前端呈现，不包含 CLI bridge 实现。默认链路是 ChatRaw 后端调用 Hermes API Server `/v1/runs`，并把 Hermes 事件流转换为 ChatRaw 的正文、思考过程和 tool activity。

## 设计原则

1. **API Server 单一路线。**
   默认 Base URL 是 `http://127.0.0.1:8642/v1`，默认 model 是 `hermes-agent`，默认 API endpoint 是 `runs`。

2. **Key 只保存在后端。**
   浏览器插件只保存路由选择和 Base URL；API Server Key 由 ChatRaw 后端保存和注入。

3. **正文优先，过程折叠。**
   对话内容默认显示；工具调用、参数、结果和思考过程保持低视觉权重，可展开检查。

4. **每个阶段走 GitHub 标准流程。**
   issue 记录需求，feature branch 承载实现，PR 做评审入口，Actions 验证测试和打包。

## 当前基线

- 仓库：`jiezhu-spec/chatraw-hermes-API-Server`
- 上游来源：`jiezhu-spec/ChatRaw-Hermes`
- ChatRaw 端口：`51234`
- Hermes API Server：`http://127.0.0.1:8642/v1`
- Docker Compose 服务：`chatraw-hermes-api-server`
- Release 镜像：`ghcr.io/jiezhu-spec/chatraw-hermes-api-server`

## Phase 0: 仓库拆分

- 创建新 GitHub 仓库。
- 保留 upstream 指向原 `ChatRaw-Hermes`。
- 创建 issue 记录 API Server 专用改造。
- 从 feature branch 提 PR。
- 使用 Actions 验证 Python、前端静态资源和 Docker build。

验收：

- main 保持可部署。
- PR 关联 issue。
- Actions 通过后再合并。

## Phase 1: API Server 默认链路

- 默认 `apiMode` 改为 `runs`。
- 插件 manifest 和前端设置默认 `runs`。
- 后端缺省配置也走 `/v1/runs`。
- 保留 Chat Completions endpoint 作为 API Server 兼容端点，不作为默认路径。

验收：

- 缺少 `apiMode` 的旧配置也默认走 `/v1/runs`。
- `/api/hermes/health` 通过 `/v1/models` 检查 API Server。
- `/api/hermes/chat` 使用同一 ChatRaw chat id 映射为稳定 Hermes session id。

## Phase 2: Tool Activity 和事件归一化

- 支持 message delta、thinking delta、tool start/progress/complete、error、terminal event。
- tool event 合并到同一 `tool_id`。
- 兼容 API Server 同时发送 delta 和最终完整快照的情况，避免正文重复。

验收：

- `介绍下你的能力` 不重复正文。
- 文件、时间、PPT skill 等工具链路能显示 tool activity。
- 保存的 `messages.tool_calls` 包含工具名、阶段、参数和结果。

## Phase 3: 打包与部署

- Docker Compose 服务名和镜像名改为 `chatraw-hermes-api-server`。
- CI Docker build 使用 `chatraw-hermes-api-server:test`。
- Release 发布到 `ghcr.io/jiezhu-spec/chatraw-hermes-api-server`。

验收：

- `docker compose up -d --build` 可启动。
- GitHub Actions 全绿。
- 99 服务器从 GitHub clone 后可按 README 启动。

## 本地验证命令

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m py_compile backend/main.py
.venv/bin/python -m unittest backend.test_hermes_bridge
node --check backend/static/app.js
docker build -t chatraw-hermes-api-server:test .
```

## GitHub 操作命令

```bash
gh issue create -R jiezhu-spec/chatraw-hermes-API-Server --title "API Server only baseline" --body "Split API Server mode into a dedicated repository."
git checkout -b codex/api-server-only-baseline
git add <files>
git commit -m "feat: create API server only baseline"
git push -u origin codex/api-server-only-baseline
gh pr create -R jiezhu-spec/chatraw-hermes-API-Server --base main --head codex/api-server-only-baseline
gh run list -R jiezhu-spec/chatraw-hermes-API-Server --branch codex/api-server-only-baseline
```
