# Mini Coding Agent Backend

一个基于 FastAPI、DeepSeek 大模型和 Tool Calling 的代码智能体后端项目。

本项目实现了一个简化版 Coding Agent。用户可以通过接口向 Agent 提交代码相关任务，例如读取文件、搜索代码、查看指定行、创建文件、修改文件、运行语法检查、查看 Git 状态和 Git diff。系统支持工具调用、执行轨迹记录、人工确认机制、风险分级、流式执行、单元测试和 Agent 任务评估。

---

## 1. 项目简介

Mini Coding Agent Backend 是一个面向代码任务的后端智能体系统，核心目标是模拟真实 Coding Agent 的基础工作流程：

```text
用户提出代码任务
↓
大模型分析任务
↓
模型选择合适工具
↓
后端执行工具
↓
工具结果返回给模型
↓
模型继续推理或生成最终回答
```

本项目不是普通聊天机器人，而是一个具备代码工具调用能力的 Agent 后端系统。

---

## 2. 技术栈

```text
后端框架：FastAPI
大模型调用：DeepSeek API / OpenAI SDK Compatible API
Agent 架构：原生 Agent Loop + Tool Calling
数据校验：Pydantic
流式输出：Server-Sent Events
测试框架：pytest
评估方式：自定义 Agent Eval
工程脚本：Python scripts
部署准备：Dockerfile / .dockerignore
版本管理：Git
```
---

## 项目文档

本项目提供了较完整的工程文档，方便理解接口、架构、安全机制和评估方式。

| 文档 | 说明 |
|---|---|
| [API 文档](docs/API.md) | 说明后端接口、请求示例、响应结构和推荐测试顺序 |
| [架构说明](docs/ARCHITECTURE.md) | 说明 FastAPI、Agent Loop、Tool Runner、工具系统、审批系统和日志系统的整体设计 |
| [安全机制](docs/SECURITY.md) | 说明 workspace 沙盒、路径限制、敏感文件保护、命令白名单和高风险审批机制 |
| [评估说明](docs/EVALUATION.md) | 说明 Agent Eval 的任务设计、评估指标、当前结果和后续升级方向 |
| [路线图](docs/ROADMAP.md) | 说明当前版本完成度、后续迭代计划和长期演进方向 |
---


## 3. 核心功能

### 3.1 CodeAgent 代码智能体

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

### 3.2 SSE 流式执行接口

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

### 3.3 人工确认机制

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

---

## 4. 工具系统

### 4.1 只读工具

| 工具名 | 作用 |
|---|---|
| list_files | 查看 workspace 中的文件和目录 |
| read_file | 读取小文件全文 |
| read_file_lines | 读取指定行范围 |
| search_code | 搜索代码关键词 |
| get_file_diff | 查看单个文件与 .bak 的差异 |
| get_workspace_diff | 查看 workspace 中的 .bak diff |
| get_git_status | 查看 Git 工作区状态 |
| get_git_diff | 查看 Git diff |

---

### 4.2 写入工具

| 工具名 | 作用 |
|---|---|
| edit_file | 精确替换已有文件内容 |
| write_new_file | 创建新文件 |
| ensure_gitignore | 创建或更新 .gitignore |

写入工具属于高风险工具，必须经过用户确认后才会执行。

---

### 4.3 命令工具

| 工具名 | 作用 |
|---|---|
| run_command | 执行安全白名单命令 |

当前允许执行的命令包括：

```text
python --version
python -m py_compile <file>
python -m pytest
pytest
```

不在白名单中的命令会被拒绝。

---

## 5. 工具风险分级

项目将工具分为三类风险等级：

| 风险等级 | 工具类型 | 是否需要审批 |
|---|---|---|
| low | 只读工具 | 否 |
| medium | 命令执行工具 | 暂不审批，但必须白名单限制 |
| high | 写入工具 | 是 |

示例：

```text
read_file_lines → low
run_command → medium
write_new_file → high
```

风险分级统一由以下文件管理：

```text
app/agent/tool_policy.py
```

---

## 6. Approval Resume

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

## 7. Agent Trace 日志

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

## 8. 历史记录接口

### 查看历史运行列表

```text
GET /agent/runs
```

### 查看单次运行详情

```text
GET /agent/runs/{run_id}
```

---

## 9. 项目结构

```text
mini-agent-backend/
├── app/
│   ├── __init__.py
│   ├── schemas.py
│   ├── llm/
│   │   ├── __init__.py
│   │   └── deepseek_client.py
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
│   ├── test_file_tools.py
│   ├── test_git_tools.py
│   ├── test_approval_store.py
│   └── test_run_logger.py
├── workspace/
│   ├── .agent_runs/
│   ├── .agent_pending/
│   └── demo_project/
├── eval_tasks.json
├── eval_result.json
├── run_eval.py
├── main.py
├── requirements.txt
├── pytest.ini
├── .env
├── .gitignore
└── README.md
```
---

## 项目核心亮点

本项目不是简单的 LLM 聊天机器人，而是一个后端优先的 Coding Agent 原型系统。

核心亮点包括：

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

6. Git 工具隔离
   get_git_status 和 get_git_diff 只允许作用于独立 Git 仓库根目录，避免 workspace 普通目录误读主项目 Git 状态。

7. SSE 流式执行过程
   支持实时输出 Agent 执行事件，包括模型调用、工具调用、审批等待、最终回答等过程。

8. 运行日志记录
   每次 Agent 执行都会保存 run_id、steps、tool_result、status、error 等信息，方便调试和历史追踪。

9. Agent Eval 评估
   使用固定任务集评估 Agent 是否能稳定完成文件读取、代码搜索、Git 状态查看、命令执行和审批触发等任务。

10. 工程化交付
    提供 pytest、项目自检脚本、统一开发命令、Demo Workspace 初始化脚本、Dockerfile 和 .dockerignore。

---

## 快速启动

### 1. 创建虚拟环境

```bash
python -m venv .venv
```

### 2. 激活虚拟环境

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

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
DEEPSEEK_MODEL=deepseek-v4-flash
```

注意：

```text
.env 是本地真实配置文件，不能提交到 Git。
.env.example 是配置模板，可以提交到 Git。
```

### 5. 初始化 Demo 工作区

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

### 6. 启动后端服务

方式一：直接启动

```bash
uvicorn main:app --reload
```

方式二：使用统一开发命令

```powershell
python scripts/dev.py serve
```

启动后访问：

```text
http://127.0.0.1:8000/docs
```

### 7. 健康检查

服务启动后，另开一个终端运行：

```powershell
python scripts/dev.py health
```

预期输出：

```text
HTTP 状态码：200
服务运行正常
```

### 8. 运行单元测试

```powershell
python scripts/dev.py test
```

等价于：

```powershell
python -m pytest tests/
```

当前测试结果：

```text
23 passed
```

### 9. 运行 Agent Eval

先确保后端服务正在运行，然后执行：

```powershell
python scripts/dev.py eval
```

当前评估结果：

```text
总任务数：10
通过：10
失败：0
成功率：100.0%
```

### 10. 运行项目自检

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

## 统一开发命令

项目提供统一开发命令入口：

```text
scripts/dev.py
```

常用命令如下：

```powershell
python scripts/dev.py serve       # 启动 FastAPI 服务
python scripts/dev.py health      # 检查服务健康状态
python scripts/dev.py test        # 运行 pytest 单元测试
python scripts/dev.py eval        # 运行 Agent Eval
python scripts/dev.py check       # 运行项目自检
python scripts/dev.py setup-demo  # 初始化 demo_project 工作区
```

这样做可以减少手动记忆命令的成本，也方便项目演示和后续接入 CI/CD。

---

## Demo Workspace 说明

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

这保证了项目环境可复现，也避免污染主项目 Git 仓库。

---

## 11. 常用接口示例

### 11.1 CodeAgent 普通执行

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

### 11.2 CodeAgent 流式执行

```text
POST /agent/code/stream
```

PowerShell 测试示例：

```powershell
curl.exe -N -X POST "http://127.0.0.1:8000/agent/code/stream" -H "Content-Type: application/json" --data-binary "@body_stream.json"
```

---

### 11.3 审批执行

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

## 12. 安全机制

本项目实现了多层安全限制。

### 12.1 workspace 限制

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

### 12.2 禁止读取 .env

文件读取工具禁止读取 `.env` 文件，防止 API Key 泄露。

---

### 12.3 写操作审批

以下工具必须经过用户确认：

```text
edit_file
write_new_file
ensure_gitignore
```

---

### 12.4 命令白名单

`run_command` 只允许执行有限安全命令。

危险命令会返回：

```text
command_not_allowed
```

---

### 12.5 工具错误结构化

工具失败时会返回结构化错误，例如：

```json
{
  "type": "command_failed",
  "message": "命令执行失败，退出码不为 0。",
  "detail": "..."
}
```

---

## 13. 单元测试

项目使用 pytest 进行测试。

运行全部测试：

```bash
python -m pytest tests/
```

当前测试结果：

```text
23 passed
```

已覆盖：

- 文件工具
- Git 工具
- 审批存储
- 运行日志

---

## 14. Agent 评估集

项目提供 Agent 任务评估脚本：

```text
eval_tasks.json
run_eval.py
```

运行评估：

```bash
python run_eval.py
```

当前评估结果：

```text
总任务数：10
通过：10
失败：0
成功率：100.0%
平均耗时：7013 ms
平均工具调用数：1.60
```

评估结果会保存到：

```text
eval_result.json
```

---

## 15. 当前项目亮点

- 基于 FastAPI 构建 Agent 后端服务
- 接入 DeepSeek 大模型 Tool Calling 能力
- 实现 Agent Loop 多步工具调用
- 实现安全文件工具系统
- 支持 read_file_lines 局部代码读取
- 支持 Git status / Git diff 审查
- 支持写操作 Human-in-the-loop 审批
- 支持 Approval Resume v1
- 支持 Agent Trace 日志持久化
- 支持 SSE 流式执行过程输出
- 支持工具风险分级
- 支持结构化错误处理
- 支持 pytest 单元测试
- 支持 Agent Eval 任务评估集

---

## 16. 当前进度

当前已完成：

## 当前完成度

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
工具风险等级
运行日志记录
Agent Eval
pytest 单元测试
统一开发命令
Demo Workspace 初始化
Docker 部署准备
```

---

## 17. 后续计划

会话持久化记忆
上下文压缩
Self-Reflection 失败自省循环
代码结构索引
极简前端演示页面
LangGraph 状态机版本