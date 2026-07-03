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

| 类型 | 技术 |
|---|---|
| Web 框架 | FastAPI |
| ASGI 服务 | Uvicorn |
| 大模型接口 | DeepSeek API，OpenAI-compatible SDK |
| 配置管理 | python-dotenv |
| 数据校验 | Pydantic |
| 工具调用 | Tool Calling / Function Calling |
| 流式输出 | SSE / StreamingResponse |
| 测试框架 | pytest |
| HTTP 调用 | requests |
| 版本管理 | Git |

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

## 10. 快速启动

### 10.1 创建虚拟环境

```bash
python -m venv .venv
```

### 10.2 激活虚拟环境

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

### 10.3 安装依赖

```bash
pip install -r requirements.txt
```

### 10.4 配置环境变量

在项目根目录创建 `.env` 文件：

```env
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

注意：`.env` 不应该提交到 Git。

### 10.5 启动服务

```bash
uvicorn main:app --reload
```

启动后访问：

```text
http://127.0.0.1:8000/docs
```

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

```text
基础 FastAPI 服务
DeepSeek API 接入
Tool Calling
Agent Loop
CodeAgent
文件工具系统
Git 工具
人工确认机制
Approval Resume v1
SSE 流式执行接口
Trace 日志
历史记录接口
pytest 单元测试
Agent Eval 评估集
README 初稿
```

---

## 17. 后续计划

- 增加更完整的 Agent Resume checkpoint
- 增加更多代码编辑工具
- 增加目录创建工具
- 增加更丰富的测试集
- 增加 Docker 部署
- 增加最小演示前端
- 完善项目截图和演示文档
- 整理简历项目描述和面试讲解稿