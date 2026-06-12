# ChatRaw-Hermes 项目计划

## 目标

把 Hermes Client 中验证过的能力移植到 ChatRaw：

- 通过 ChatRaw 向 Hermes CLI / TUI Gateway 链路发送消息。
- 对话正文默认直接展示。
- 思考过程、工具调用、命令参数、执行结果默认折叠，用户可展开查看。
- 保留 ChatRaw 的轻量部署体验、Docker 运行方式和本地插件机制。
- 用 GitHub 管理完整开发流程：issues、分支、pull request、Actions、打包和部署验证。

## 当前基线

- 新仓库：`jiezhu-spec/ChatRaw-Hermes`
- 上游源：`massif-01/ChatRaw`
- 默认分支：`main`
- 上游基线提交：`0cf2a9b`
- 99 现网 ChatRaw 目录：`/home/rm01/ChatRaw`
- 99 现网服务端口：`51111`
- Hermes Client 参考实现：
  - UI：`http://10.10.99.99:18888/`
  - API：`http://10.10.99.99:18889/api`
  - TUI Gateway 事件流：`message.delta`、`reasoning.delta`、`tool.start`、`tool.complete`、`message.complete`

## 设计原则

1. **先对齐数据链，再美化 UI。**
   后端必须先能稳定输出结构化事件，否则前端只能伪折叠。

2. **正文优先，过程折叠。**
   用户最关心的回答正文默认显示；工具、代码、参数和结果默认收起。

3. **兼容 OpenAI 流式接口。**
   不破坏 ChatRaw 现有 OpenAI-compatible 模型配置；Hermes 能力作为增强路径。

4. **不把音频依赖放进默认安装。**
   `faster-whisper` 和音频智能分析链路当前不接入默认版本，避免触发大依赖下载和后台进程干扰。

5. **每个阶段有 PR 和验证。**
   每个功能 issue 对应单独分支和 PR；Actions 必须能证明代码可构建、可导入、Docker 可打包。

## 阶段拆分

### Phase 0: 项目治理与基线

- 创建 `ChatRaw-Hermes` 仓库。
- 保留上游 `upstream` remote。
- 新建 GitHub issues 和里程碑。
- 调整 Actions，使 CI 在 push、PR、手动触发下都能跑。
- 将 release 打包目标改为本仓库可用的 GHCR / GitHub Release 路线，避免继续写死 `massif01/chatraw`。
- 记录 99 现网手工补丁与上游差异。

验收：

- `main` 已推送到 `jiezhu-spec/ChatRaw-Hermes`。
- `python -m py_compile backend/main.py` 通过。
- `python -m compileall backend` 通过。
- GitHub issue、分支、PR 流程可用。

### Phase 1: 后端 Hermes 事件模型

- 定义 ChatRaw 内部事件格式：
  - `message.delta`
  - `thinking.delta`
  - `tool.start`
  - `tool.complete`
  - `message.complete`
  - `error`
- 新增 Hermes adapter，把 Hermes/TUI Gateway 或 OpenAI-compatible bridge 的事件统一映射为 ChatRaw 事件。
- 保留原 `/api/chat` 行为，同时新增或扩展流式响应格式，避免破坏已有前端。
- 保存 assistant 消息时保留：
  - 正文 `content`
  - 思考 `thinking`
  - 工具调用 `tool_calls`
  - 图片/附件引用（后续扩展）

验收：

- 单元测试覆盖事件解析和降级逻辑。
- 现有 OpenAI-compatible 模型路径仍可用。
- Hermes 工具调用能被保存为结构化数据，而不是拼接到正文里。

### Phase 2: 前端渲染迁移

参考 Hermes Client 的有效设计：

- Markdown：GFM 表格、列表、引用、代码块。
- 字号：
  - 正文 `14px`
  - 行高 `1.65`
  - 工具标题约 `11.5px`
  - 行内代码约 `12.8px`
  - 输入框约 `14.4px`
- 气泡：
  - padding `8px 16px`
  - 最大宽度桌面约 `70%`
  - 工具/思考块低视觉权重
- 默认折叠：
  - `Thinking`
  - `Tool execution`
  - 每个工具调用的 args/result

验收：

- 普通对话内容默认显示。
- 工具过程默认收起，可展开查看参数和结果。
- 长表格、长代码、长命令输出不会撑破布局。
- 移动端不重叠、不横向撑破。

### Phase 3: 持久化会话与延迟优化

- 支持复用 Hermes session key。
- 避免每轮重新创建 CLI 进程。
- 删除会话时向 Hermes/TUI Gateway 发 `session.close` 或提供清理任务。
- 显示连接状态、运行中工具状态和错误恢复提示。

验收：

- 同一 ChatRaw 会话内第二轮延迟显著低于第一轮。
- 删除/关闭会话后不长期积累 `slash_worker`。
- 99 上连续多轮问答无串话。

### Phase 4: 打包、部署和回归

- GitHub Actions：
  - Python 语法和导入检查。
  - 前端静态资源检查。
  - Docker build。
  - 可选安全扫描。
- Release：
  - tag 触发 GitHub Release。
  - 构建 Docker image。
  - 生成部署说明。
- 99 灰度部署：
  - 新容器或新端口验证。
  - 不直接破坏现有 `51111`。
  - 验证后再切换。

验收：

- Actions 全绿。
- Docker 镜像可启动。
- `/api/models/verify`、`/api/chat`、Hermes 流式工具调用均通过。

## GitHub Issue 规划

1. `Project setup: baseline, CI, release workflow`
2. `Backend: normalize Hermes streaming events`
3. `Backend: persist thinking and tool calls`
4. `Frontend: Markdown/GFM/code rendering upgrade`
5. `Frontend: collapsible thinking and tool execution blocks`
6. `Runtime: persistent Hermes session lifecycle`
7. `Packaging: Docker/GHCR/release artifacts`
8. `Deployment: 99 server gray release and regression checklist`

## 风险点

- 99 现网目录不是 git 仓库，不能直接当可信源；必须以 GitHub 上游为基线，挑选补丁。
- 上游已有 Hermes 相关提交，不能回退覆盖。
- Hermes Client 删除会话后 `slash_worker` 仍可能残留，ChatRaw-Hermes 需要显式生命周期管理。
- GitHub Actions 不能继续写死 DockerHub `massif01/chatraw`。
- `faster-whisper` 会触发大依赖和模型下载，当前默认版本不接入该插件链路。
