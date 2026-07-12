# Mini Coding Agent Backend

一个基于 **FastAPI、DeepSeek API、Tool Calling 和原生 Agent Loop** 实现的代码智能体后端项目。

本项目不是普通聊天机器人，而是一个面向代码任务的后端 Agent 系统。用户可以通过接口向 Agent 提交代码相关任务，例如读取文件、搜索代码、查看指定行、创建文件、修改文件、运行语法检查、查看 Git 状态和 Git diff。系统支持工具调用、执行轨迹记录、人工确认机制、风险分级、流式执行、会话记忆、上下文管理、单元测试和 Agent 任务评估。

---

## 1. 当前版本

## v0.5.0 - Self-Reflection Retry 失败自省与重试提示

### 新增功能
- 新增 `app/agent/reflection.py`，实现规则版失败自省模块。
- 支持工具失败时生成结构化 reflection，包括：
  - 触发原因
  - 失败工具
  - 错误类型
  - 错误信息
  - 分析说明
  - 修复建议
  - 是否允许重试
  - 建议下一步工具
- Agent Loop 支持在工具失败时把 reflection 写入当前 step。
- Agent Loop 支持将 reflection 作为增强工具结果反馈给模型，引导模型下一轮重新规划。
- 新增 `retry_from_reflection` 字段，用于标记该步骤是否触发了 Reflection Retry。
- 支持 max_steps 达到上限时追加 reflection step。
- 支持旧版字符串工具结果的错误识别，例如：
  - 文件不存在
  - 路径不存在
  - 权限错误
  - 命令执行失败

### 修改内容
- `AgentStep` 新增 `reflection` 字段。
- `AgentStep` 新增 `retry_from_reflection` 字段。
- `agent_loop.py` 新增工具失败判断逻辑。
- `execute_tool()` 支持从旧工具字符串结果中推断错误。
- 更新项目自检脚本，纳入 Reflection 相关测试文件。

### 新增测试
- `tests/test_reflection.py`
- `tests/test_reflection_schema.py`
- `tests/test_agent_loop_reflection.py`
- `tests/test_agent_loop_reflection_retry.py`
- `tests/test_agent_loop_max_steps_reflection.py`
- `tests/test_agent_loop_legacy_tool_error.py`

### 说明
- 当前 Reflection 是规则版，不依赖额外 LLM 调用。
- 当前每次 Agent 任务最多触发一次 Reflection Retry，避免无限重试。
- 当前 Reflection Retry 不会重新启动整个任务，而是在同一个 Agent Loop 中把失败自省结果反馈给模型。

```text
v0.4.0
```

当前版本为：

```text
Summary Memory 摘要记忆基础版本
```

在 v0.2.0 会话记忆能力的基础上，v0.3.0 新增了 Context Manager，用于控制发送给大模型的历史消息数量和上下文 token 预算。

---

## 2. 项目目标

Mini Coding Agent Backend 的核心目标是模拟真实 Coding Agent 的基础工作流程：

```text
用户提出代码任务
↓
大模型分析任务
↓
模型选择合适工具
↓
后端校验并执行工具
↓
工具结果返回给模型
↓
模型继续推理或生成最终回答
```

对于带记忆的聊天场景，流程是：

```text
用户发送消息
↓
后端创建或读取 conversation_id
↓
保存 user message
↓
Context Manager 构建上下文窗口
↓
调用大模型
↓
保存 assistant message
↓
返回 answer + context_stats
```

---

## 3. 技术栈

```text
后端框架：FastAPI
大模型调用：DeepSeek API / OpenAI SDK Compatible API
Agent 架构：原生 Agent Loop + Tool Calling
数据校验：Pydantic
流式输出：Server-Sent Events
会话存储：本地 JSON
上下文管理：Context Manager
测试框架：pytest
评估方式：自定义 Agent Eval
工程脚本：Python scripts
部署准备：Dockerfile / .dockerignore
版本管理：Git / GitHub Release
```

---

## 4. 项目文档

| 文档 | 说明 |
|---|---|
| [API 文档](docs/API.md) | 说明后端接口、请求示例、响应结构和推荐测试顺序 |
| [架构说明](docs/ARCHITECTURE.md) | 说明 FastAPI、Agent Loop、Tool Runner、工具系统、审批系统和日志系统的整体设计 |
| [安全机制](docs/SECURITY.md) | 说明 workspace 沙盒、路径限制、敏感文件保护、命令白名单和高风险审批机制 |
| [评估说明](docs/EVALUATION.md) | 说明 Agent Eval 的任务设计、评估指标、当前结果和后续升级方向 |
| [路线图](docs/ROADMAP.md) | 说明当前版本完成度、后续迭代计划和长期演进方向 |

---

## 5. 核心功能

### 5.1 CodeAgent 代码智能体

核心接口：

```text
POST /agent/code
```

CodeAgent 支持：

- 查看 workspace 文件结构
- 读取文件
- 按行读取局部代码
- 搜索代码关键词
- 创建新文件
- 修改已有文件
- 查看 workspace diff
- 查看 Git status
- 查看 Git diff
- 运行安全白名单命令
- 保存执行轨迹
- 生成最终任务总结

---

### 5.2 SSE 流式执行接口

流式接口：

```text
POST /agent/code/stream
```

该接口会实时返回 Agent 执行过程中的事件，例如：

```text
agent_started
model_call_started
model_call_finished
tool_call_started
tool_call_finished
approval_required
final_answer
agent_finished
agent_failed
max_steps_reached
```

这样前端可以实时展示 Agent 当前正在执行的步骤。

---

### 5.3 会话记忆能力

从 v0.2.0 开始，项目支持基础会话记忆。

核心接口：

```text
POST /chat/memory
```

支持能力：

- 不传 `conversation_id` 时自动创建新会话
- 传入 `conversation_id` 时继续已有会话
- 自动保存用户消息
- 自动保存助手回复
- 支持本地 JSON 会话持久化
- 支持查看会话列表和会话详情

会话数据保存在：

```text
workspace/.conversations/
```

该目录属于运行时数据，不会提交到 Git。

---

### 5.4 Context Manager 上下文管理

从 v0.3.0 开始，`/chat/memory` 接入 Context Manager。

Context Manager 负责：

- 规范化历史消息
- 按消息数量裁剪历史记录
- 粗略估算输入 token 数量
- 按 token 预算裁剪上下文
- 返回上下文使用统计信息

`/chat/memory` 响应中会返回：

```json
{
  "context_stats": {
    "total_messages": 4,
    "used_messages": 4,
    "dropped_messages": 0,
    "estimated_input_tokens": 123,
    "max_context_tokens": 6000,
    "reserved_output_tokens": 800
  }
}
```

这让系统从“简单保存历史”升级为“有上下文预算意识的多轮会话”。

---

### 5.5 人工确认机制

对于高风险工具，例如：

```text
edit_file
write_new_file
ensure_gitignore
```

Agent 不会直接执行，而是返回：

```text
status = waiting_approval
```

同时生成：

```text
pending_action
approval_id
```

用户通过审批接口确认或拒绝：

```text
POST /agent/approvals/{approval_id}/execute
```

请求体示例：

```json
{
  "approved": true
}
```

或：

```json
{
  "approved": false
}
```

### Summary Memory 摘要记忆

从 v0.4.0 开始，项目支持 Summary Memory 摘要记忆基础能力。

Summary Memory 用于将较早的历史对话压缩成长期摘要，并在后续 `/chat/memory` 请求中作为长期上下文注入模型。

核心接口：

```text
GET /conversations/{conversation_id}/summary
PUT /conversations/{conversation_id}/summary
POST /conversations/{conversation_id}/summary/refresh
```
支持能力：

保存会话摘要
读取会话摘要
根据未摘要消息自动刷新摘要
使用 source_message_count 跟踪摘要覆盖到第几条消息
在 /chat/memory 中通过 summary_used 判断本次是否使用了摘要

---

## 6. 主要接口

### 6.1 基础接口

| 接口 | 说明 |
|---|---|
| `GET /health` | 健康检查 |
| `POST /chat` | 普通单轮聊天 |
| `POST /chat/history` | 带请求内历史的聊天 |
| `POST /chat/stream` | 普通流式聊天 |

---

### 6.2 CodeAgent 接口

| 接口 | 说明 |
|---|---|
| `POST /agent/code` | 执行代码智能体任务 |
| `POST /agent/code/stream` | 流式执行代码智能体任务 |
| `GET /agent/runs` | 查看 Agent 运行历史 |
| `GET /agent/runs/{run_id}` | 查看单次运行详情 |
| `POST /agent/approvals/{approval_id}/execute` | 执行或拒绝高风险操作审批 |

---

### 6.3 会话记忆接口

| 接口 | 说明 |
|---|---|
| `POST /conversations` | 创建新会话 |
| `GET /conversations` | 查看最近会话列表 |
| `GET /conversations/{conversation_id}` | 查看某个会话详情 |
| `POST /conversations/{conversation_id}/messages` | 向指定会话追加消息 |
| `POST /chat/memory` | 带本地会话记忆的聊天接口 |

---

## 7. 工具系统

### 7.1 只读工具

| 工具名 | 作用 | 风险等级 |
|---|---|---|
| `list_files` | 查看 workspace 中的文件和目录 | low |
| `read_file` | 读取小文件全文 | low |
| `read_file_lines` | 读取指定行范围 | low |
| `search_code` | 搜索代码关键词 | low |
| `get_file_diff` | 查看单个文件与 `.bak` 的差异 | low |
| `get_workspace_diff` | 查看 workspace 中的 `.bak diff` | low |
| `get_git_status` | 查看 Git 工作区状态 | low |
| `get_git_diff` | 查看 Git diff | low |

---

### 7.2 写入工具

| 工具名 | 作用 | 风险等级 |
|---|---|---|
| `edit_file` | 精确替换已有文件内容 | high |
| `write_new_file` | 创建新文件 | high |
| `ensure_gitignore` | 创建或更新 `.gitignore` | high |

写入工具属于高风险工具，必须经过用户确认后才会执行。

---

### 7.3 命令工具

| 工具名 | 作用 | 风险等级 |
|---|---|---|
| `run_command` | 执行安全白名单命令 | medium |

当前允许执行的命令包括：

```text
python --version
python -m py_compile <file>
python -m pytest
pytest
```

不在白名单中的命令会被拒绝。

---

## 8. 工具风险分级

项目将工具分为四类风险等级：

| 风险等级 | 工具类型 | 是否需要审批 |
|---|---|---|
| low | 只读工具 | 否 |
| medium | 命令执行工具 | 暂不审批，但必须白名单限制 |
| high | 写入工具 | 是 |
| unknown | 未登记工具 | 默认谨慎处理 |

风险分级统一由以下文件管理：

```text
app/agent/tool_policy.py
```

---

## 9. Approval Resume

项目支持 Approval Resume v1。

流程如下：

```text
Agent 请求高风险工具
↓
后端返回 waiting_approval
↓
用户批准
↓
工具执行
↓
重新构造续跑任务
↓
CodeAgent 继续执行后续检查和总结
```

例如用户要求创建文件并运行语法检查时：

```text
创建文件
↓
等待用户确认
↓
用户批准
↓
执行 write_new_file
↓
自动继续 run_command
↓
自动继续 get_git_status
↓
生成最终总结
```

审批接口返回中包含：

```text
resume_result
```

---

## 10. Agent Trace 日志

每次 Agent 执行都会保存运行日志。

日志目录：

```text
workspace/.agent_runs/
```

日志中包含：

- run_id
- agent_name
- model_name
- status
- user_message
- answer
- max_steps
- steps
- error
- pending_action
- log_path

每个 step 包含：

```text
step
type
tool_name
tool_args
risk_level
tool_result
success
error
started_at
ended_at
duration_ms
```

---

## 11. 项目结构

```text
mini-agent-backend/
├── app/
│   ├── __init__.py
│   ├── schemas.py
│   ├── llm/
│   │   ├── __init__.py
│   │   └── deepseek_client.py
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── conversation_store.py
│   │   └── context_manager.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── time_tools.py
│   │   ├── text_tools.py
│   │   └── file_tools.py
│   └── agent/
│       ├── __init__.py
│       ├── agent_loop.py
│       ├── agent_stream.py
│       ├── approval_store.py
│       ├── code_agent.py
│       ├── hello_agent.py
│       ├── run_logger.py
│       ├── status.py
│       ├── tool_policy.py
│       └── tool_runner.py
├── tests/
│   ├── test_approval_store.py
│   ├── test_context_manager.py
│   ├── test_conversation_store.py
│   ├── test_file_tools.py
│   ├── test_git_tools.py
│   ├── test_memory_chat_api.py
│   └── test_run_logger.py
├── workspace/
│   ├── .agent_runs/
│   ├── .agent_pending/
│   ├── .conversations/
│   └── demo_project/
├── docs/
│   ├── API.md
│   ├── ARCHITECTURE.md
│   ├── SECURITY.md
│   ├── EVALUATION.md
│   └── ROADMAP.md
├── scripts/
│   ├── check_project.py
│   ├── check_release.py
│   ├── dev.py
│   └── setup_demo_workspace.py
├── eval_tasks.json
├── run_eval.py
├── main.py
├── requirements.txt
├── pytest.ini
├── VERSION
├── CHANGELOG.md
├── Dockerfile
├── .dockerignore
├── .env.example
├── .gitignore
└── README.md
```

---

## 12. 快速启动

### 12.1 创建虚拟环境

```bash
python -m venv .venv
```

### 12.2 激活虚拟环境

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

### 12.3 安装依赖

```bash
pip install -r requirements.txt
```

### 12.4 配置环境变量

项目提供了环境变量模板：

```text
.env.example
```

复制模板文件：

```powershell
copy .env.example .env
```

然后打开 `.env`，填写自己的 DeepSeek API Key：

```env
DEEPSEEK_API_KEY=your_real_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

注意：

```text
.env 是本地真实配置文件，不能提交到 Git。
.env.example 是配置模板，可以提交到 Git。
```

---

### 12.5 初始化 Demo 工作区

本项目的 CodeAgent 会在 `workspace/demo_project` 中执行代码读取、搜索、Git status、Git diff 和语法检查等演示任务。

首次运行项目前，建议执行：

```powershell
python scripts/setup_demo_workspace.py --reset
```

或者使用统一开发命令：

```powershell
python scripts/dev.py setup-demo
```

该脚本会自动完成：

```text
创建 demo_project 示例文件
初始化 demo_project Git 仓库
创建 baseline commit
制造可用于 git diff 的未提交改动
准备 Agent Eval 所需文件
```

---

### 12.6 启动后端服务

方式一：直接启动：

```bash
uvicorn main:app --reload
```

方式二：使用统一开发命令：

```powershell
python scripts/dev.py serve
```

启动后访问：

```text
http://127.0.0.1:8000/docs
```

---

### 12.7 健康检查

服务启动后，另开一个终端运行：

```powershell
python scripts/dev.py health
```

预期输出：

```text
HTTP 状态码：200
服务运行正常
```

---

## 13. 常用开发命令

项目提供统一开发命令入口：

```text
scripts/dev.py
```

常用命令如下：

```powershell
python scripts/dev.py serve          # 启动 FastAPI 服务
python scripts/dev.py health         # 检查服务健康状态
python scripts/dev.py test           # 运行 pytest 单元测试
python scripts/dev.py eval           # 运行 Agent Eval
python scripts/dev.py check          # 运行项目自检
python scripts/dev.py setup-demo     # 初始化 demo_project 工作区
python scripts/dev.py release-check  # 发布前安全检查
```

---

## 14. 接口测试示例

### 14.1 CodeAgent 普通执行

```text
POST /agent/code
```

请求体：

```json
{
  "message": "请读取 demo_project/math_utils.py 第 1 到 20 行，并解释代码作用。",
  "max_steps": 5
}
```

---

### 14.2 CodeAgent 流式执行

```text
POST /agent/code/stream
```

PowerShell 测试示例：

```powershell
curl.exe -N -X POST "http://127.0.0.1:8000/agent/code/stream" -H "Content-Type: application/json" --data-binary "@body_stream.json"
```

---

### 14.3 创建会话

```text
POST /conversations
```

请求体：

```json
{
  "title": "Day 08 Memory Test",
  "metadata": {
    "source": "manual_test"
  }
}
```

---

### 14.4 带记忆聊天

```text
POST /chat/memory
```

请求体：

```json
{
  "message": "我正在做 Mini Coding Agent 项目，请记住我在做上下文管理功能。",
  "title": "Context Manager Test",
  "max_tokens": 800,
  "max_history_messages": 20,
  "max_context_tokens": 6000,
  "metadata": {
    "source": "manual_test"
  }
}
```

第二轮继续同一个会话：

```json
{
  "conversation_id": "conv_xxxxxxxx_xxxxxx_xxxxxxxx",
  "message": "我刚才说我在做什么功能？"
}
```

---

### 14.5 审批执行

```text
POST /agent/approvals/{approval_id}/execute
```

请求体：

```json
{
  "approved": true
}
```

---

## 15. 安全机制

本项目实现了多层安全限制。

### 15.1 workspace 限制

所有文件工具只能访问：

```text
workspace/
```

禁止访问项目外路径。

例如以下路径会被拒绝：

```text
../outside.txt
C:\Users\...
```

---

### 15.2 禁止读取 .env

文件读取工具禁止读取 `.env` 文件，防止 API Key 泄露。

---

### 15.3 写操作审批

以下工具必须经过用户确认：

```text
edit_file
write_new_file
ensure_gitignore
```

---

### 15.4 命令白名单

`run_command` 只允许执行有限安全命令。

危险命令会返回：

```text
command_not_allowed
```

---

### 15.5 工具错误结构化

工具失败时会返回结构化错误，例如：

```json
{
  "type": "command_failed",
  "message": "命令执行失败，退出码不为 0。",
  "detail": "..."
}
```

---

## 16. 单元测试

项目使用 pytest 进行测试。

运行全部测试：

```bash
python -m pytest tests/
```

或者：

```powershell
python scripts/dev.py test
```

当前测试结果：

```text
38 passed
```

已覆盖：

- 文件工具
- Git 工具
- 审批存储
- 运行日志
- 会话存储
- 记忆聊天接口
- 上下文管理模块

---

## 17. Agent 评估集

项目提供 Agent 任务评估脚本：

```text
eval_tasks.json
run_eval.py
```

运行评估：

```bash
python run_eval.py
```

或：

```powershell
python scripts/dev.py eval
```

当前评估结果：

```text
总任务数：10
通过：10
失败：0
成功率：100.0%
平均工具调用数：约 1.60
```

评估结果会保存到：

```text
eval_result.json
```

该文件属于运行时结果，不提交到 Git。

---

## 18. 项目自检

运行：

```powershell
python scripts/dev.py check
```

项目自检会检查：

```text
关键文件是否存在
.env.example 是否存在
requirements.txt 是否完整
.gitignore 是否包含关键规则
pytest 单元测试是否通过
```

预期结果：

```text
通过项：5/5
项目自检通过
```

---

## 19. Demo Workspace 说明

`workspace/demo_project` 是 CodeAgent 的演示工作区。

它用于演示：

```text
文件读取
代码搜索
局部代码读取
Git status
Git diff
语法检查
文件创建与修改
```

该目录由脚本自动生成：

```powershell
python scripts/setup_demo_workspace.py --reset
```

主项目不会提交 `workspace/demo_project`，原因是：

```text
demo_project 是可再生的演示环境
其中包含独立的 .git 仓库
不同机器上可以通过脚本重新生成
避免把运行时数据和嵌套 Git 仓库提交到主项目
```

---

## 20. 项目核心亮点

```text
1. 原生手写 Agent Loop
   不直接套壳 LangChain，通过自定义循环实现模型调用、工具调用、工具结果回填和多步推理。

2. Tool Calling 工具闭环
   模型只负责规划工具调用，后端负责校验和执行工具，避免模型直接操作系统。

3. 受控 Workspace 沙盒
   所有文件读取、搜索、修改和命令执行都限制在 workspace 目录内，防止越权访问项目外文件。

4. 高风险操作审批机制
   edit_file、write_new_file、ensure_gitignore 等会修改文件的工具必须经过用户审批后才能执行。

5. 工具风险等级设计
   将工具划分为 low、medium、high、unknown，方便后续扩展权限控制和前端审批页面。

6. Approval Resume
   用户批准高风险操作后，Agent 可以继续执行后续检查和总结。

7. Git 工具隔离
   get_git_status 和 get_git_diff 只允许作用于独立 Git 仓库根目录，避免 workspace 普通目录误读主项目 Git 状态。

8. SSE 流式执行过程
   支持实时输出 Agent 执行事件，包括模型调用、工具调用、审批等待、最终回答等过程。

9. 运行日志记录
   每次 Agent 执行都会保存 run_id、steps、tool_result、status、error 等信息，方便调试和历史追踪。

10. 会话记忆能力
    支持 conversation_id、本地 JSON 会话持久化、多轮记忆聊天和历史消息管理。

11. 上下文管理能力
    支持历史消息裁剪、token 预算控制和 context_stats 统计返回。

12. Agent Eval 评估
    使用固定任务集评估 Agent 是否能稳定完成文件读取、代码搜索、Git 状态查看、命令执行和审批触发等任务。

13. 工程化交付
    提供 pytest、项目自检脚本、统一开发命令、Demo Workspace 初始化脚本、Dockerfile 和 .dockerignore。
```

---

## 21. 当前完成度

当前版本已经完成：

```text
基础 LLM 对话
普通流式输出
CodeAgent 主接口
CodeAgent SSE 流式接口
文件读取工具
局部代码读取工具
代码搜索工具
文件创建工具
文件修改工具
安全命令执行工具
Git status / Git diff 工具
高风险操作审批
审批后继续执行
工具风险等级
运行日志记录
Agent Eval
pytest 单元测试
统一开发命令
Demo Workspace 初始化
Docker 部署准备
会话记忆基础能力
上下文管理基础能力
```

---

## 22. 后续计划

后续迭代方向：

```text
v0.5.0：Self-Reflection 失败自省循环
v0.6.0：代码结构索引与更强代码检索
v0.7.0：极简前端演示页面
v0.8.0：LangGraph 状态机版本
v0.9.0：评估体系升级
v1.0.0：完整 Mini Coding Agent Demo 版本
```

---

## 23. 版本记录

### v0.1.0

```text
完成基础 Agent Loop
完成 Tool Calling
完成文件工具、Git 工具、命令工具
完成高风险审批机制
完成运行日志
完成 SSE 流式输出
完成 Agent Eval
完成基础工程化封版
```


### v0.2.0

```text
新增 conversation_id
新增本地 JSON 会话持久化
新增 /conversations 系列接口
新增 /chat/memory 多轮记忆聊天接口
测试数量增加到 33 passed
```


### v0.3.0

```text
新增 Context Manager
支持上下文窗口构建
支持历史消息裁剪
支持 token 预算控制
/chat/memory 返回 context_stats
测试数量增加到 38 passed
```


### v0.4.0

```text
新增 Summary Memory 摘要记忆基础能力
新增会话 summary 字段
支持读取和手动更新会话摘要
新增 GET /conversations/{conversation_id}/summary 接口
新增 PUT /conversations/{conversation_id}/summary 接口
新增 POST /conversations/{conversation_id}/summary/refresh 自动摘要刷新接口
支持根据 source_message_count 进行增量摘要
/chat/memory 支持注入 Summary Memory
context_stats 新增 summary_used 字段
新增 summary_manager.py
测试数量增加到 49 passed
```


### v0.5.0：Self-Reflection Retry

本版本新增失败自省与重试提示能力。当工具调用失败时，系统会根据错误类型生成结构化 reflection，记录失败原因、修复建议、是否允许重试以及建议下一步工具。Agent Loop 会将 reflection 写入执行步骤，并在允许重试时把 reflection 作为增强上下文反馈给模型，引导模型下一轮重新规划工具调用。

当前支持的典型场景包括：
- 文件不存在
- 路径不存在
- 权限或安全策略错误
- 命令执行失败
- 工具参数错误
- 达到 max_steps 执行上限

该功能提升了 CodeAgent 的错误恢复能力和执行日志可解释性。
