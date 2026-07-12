# Agent 架构说明

本文档说明 Mini Coding Agent Backend 的整体架构设计。

本项目不是简单的聊天机器人，而是一个基于 Tool Calling 和 Agent Loop 的后端 Coding Agent 原型系统。

---

## 1. 项目定位

Mini Coding Agent Backend 的目标是实现一个后端优先的代码智能体系统。

它可以根据用户需求，在受控的 workspace 中完成：

```text
读取文件
局部读取代码
搜索代码
查看 Git 状态
查看 Git diff
运行安全命令
创建新文件
修改已有文件
记录执行轨迹
高风险操作审批
流式展示执行过程
```

项目重点不是前端页面，而是后端 Agent 工程能力。

---

## 2. 整体架构图

当前系统可以分为六层：

```text
用户 / 前端 / curl
        ↓
FastAPI 接口层
        ↓
Agent 编排层
        ↓
LLM Client 层
        ↓
Tool Runner 层
        ↓
Workspace 工具层
```

更具体地说：

```text
HTTP Request
   ↓
main.py
   ↓
CodeAgent
   ↓
Agent Loop
   ↓
DeepSeek LLM
   ↓
Tool Calling
   ↓
Tool Runner
   ↓
file_tools / time_tools / text_tools
   ↓
workspace/
```

---

## 3. 目录结构说明

核心目录如下：

```text
app/
├── llm/
│   └── deepseek_client.py
├── tools/
│   ├── file_tools.py
│   ├── text_tools.py
│   └── time_tools.py
├── agent/
│   ├── code_agent.py
│   ├── agent_loop.py
│   ├── agent_stream.py
│   ├── tool_runner.py
│   ├── tool_policy.py
│   ├── approval_store.py
│   ├── run_logger.py
│   └── status.py
└── schemas.py
```

各部分职责：

```text
main.py：
FastAPI 接口入口，负责接收 HTTP 请求并调用对应 Agent。

schemas.py：
定义请求体、响应体、Agent Step、运行记录、审批结果等数据结构。

llm/deepseek_client.py：
封装 DeepSeek API 调用，包括普通对话和流式输出。

agent/code_agent.py：
定义 CodeAgent 的系统提示词、工具列表和入口函数。

agent/agent_loop.py：
实现核心 Agent Loop，包括模型调用、工具调用、步骤记录、审批判断和错误处理。

agent/agent_stream.py：
实现 CodeAgent 的 SSE 流式执行过程。

agent/tool_runner.py：
根据模型返回的 tool_name 和 tool_args 执行对应工具。

agent/tool_policy.py：
定义工具风险等级和是否需要审批。

agent/approval_store.py：
保存和读取待审批动作。

agent/run_logger.py：
保存 Agent 每次运行的完整日志。

tools/file_tools.py：
实现文件读取、代码搜索、文件修改、Git status、Git diff、安全命令执行等工具。
```

---

## 4. FastAPI 接口层

FastAPI 层主要在：

```text
main.py
```

它负责：

```text
1. 定义 HTTP 接口
2. 接收用户请求
3. 校验请求体
4. 调用对应 Agent
5. 返回结构化响应
```

核心接口包括：

```text
GET  /health
POST /chat
POST /chat/history
POST /chat/stream
POST /agent/code
POST /agent/code/stream
GET  /agent/runs
GET  /agent/runs/{run_id}
POST /agent/approvals/{approval_id}/execute
```

其中最核心的是：

```text
POST /agent/code
POST /agent/code/stream
```

---

## 5. CodeAgent 层

CodeAgent 位于：

```text
app/agent/code_agent.py
```

它主要负责：

```text
1. 定义 Coding Agent 的系统提示词
2. 告诉模型可以使用哪些工具
3. 约束模型只能在 workspace 内工作
4. 引导模型先读取、再分析、再修改
5. 要求模型在创建或修改文件时使用工具
```

CodeAgent 的核心规则包括：

```text
读取文件前必须使用 read_file 或 read_file_lines
搜索代码时使用 search_code
读取搜索结果上下文时使用 read_file_lines
创建新文件使用 write_new_file
修改已有文件使用 edit_file
运行命令使用 run_command
查看 Git 状态使用 get_git_status
查看 diff 使用 get_git_diff
```

---

## 6. Agent Loop 核心流程

Agent Loop 位于：

```text
app/agent/agent_loop.py
```

它是整个项目的核心。

当前 Agent Loop 基本流程如下：

```text
1. 接收用户任务
2. 构造 system message 和 user message
3. 调用 LLM
4. 判断模型是否返回 tool_calls
5. 如果没有 tool_calls，返回最终答案
6. 如果有 tool_calls，检查工具风险等级
7. 低风险或中风险工具直接执行
8. 高风险工具进入审批流程
9. 将工具执行结果作为 tool message 写回 messages
10. 再次调用 LLM
11. 循环直到完成、失败、等待审批或达到最大步数
```

简化流程图：

```text
User Task
   ↓
Call LLM
   ↓
Need Tool?
   ├── No → Final Answer
   └── Yes
        ↓
   Check Risk Level
        ↓
   High Risk?
   ├── Yes → Save Pending Action → Waiting Approval
   └── No
        ↓
   Execute Tool
        ↓
   Append Tool Result
        ↓
   Call LLM Again
```

---

## 7. Tool Calling 机制

本项目采用 Function Calling / Tool Calling 思路。

模型不会直接执行代码。

模型只会返回类似：

```json
{
  "tool_name": "read_file_lines",
  "tool_args": {
    "path": "demo_project/main.py",
    "start_line": 1,
    "end_line": 20
  }
}
```

真正执行工具的是后端：

```text
Tool Runner
```

执行完成后，后端再把工具结果返回给模型，让模型继续判断下一步。

这保证了：

```text
模型负责规划
后端负责执行
工具负责落地
策略负责安全
```

---

## 8. 工具系统设计

工具主要集中在：

```text
app/tools/file_tools.py
```

当前文件工具包括：

```text
list_files
read_file
read_file_lines
search_code
edit_file
write_new_file
run_command
get_file_diff
get_workspace_diff
get_git_status
get_git_diff
ensure_gitignore
```

工具设计原则：

```text
1. 所有路径必须限制在 workspace 内
2. 禁止读取 .env
3. 禁止访问隐藏目录和敏感文件
4. 文件修改必须使用精确 old_text 替换
5. 新文件创建不能覆盖已有文件
6. 命令执行必须走白名单
7. Git 工具只能作用于独立 Git 仓库根目录
```

---

## 9. 工具风险等级

工具风险等级定义在：

```text
app/agent/tool_policy.py
```

当前分为三类：

```text
low：
只读工具，不修改文件。

medium：
中风险工具，例如 run_command。
虽然不直接修改文件，但可能执行外部命令。

high：
高风险工具，例如 edit_file、write_new_file、ensure_gitignore。
这些工具会修改或创建文件，必须经过审批。
```

示例：

```text
read_file_lines → low
search_code → low
run_command → medium
write_new_file → high
edit_file → high
```

---

## 10. 审批机制

审批相关代码位于：

```text
app/agent/approval_store.py
```

当 Agent 准备执行高风险工具时，系统不会立即执行，而是：

```text
1. 保存 pending action
2. 返回 waiting_approval 状态
3. 等待用户确认
4. 用户调用审批接口
5. 后端执行工具
6. 将执行结果交还给 Agent
7. Agent 继续完成后续分析
```

审批接口：

```text
POST /agent/approvals/{approval_id}/execute
```

审批机制的价值：

```text
防止模型直接修改文件
保证用户对高风险操作有最终控制权
适合前端做 Diff 确认和人工审批
```

---

## 11. 运行日志系统

运行日志相关代码位于：

```text
app/agent/run_logger.py
```

每次 Agent 执行都会保存完整日志，包括：

```text
run_id
agent_name
model_name
status
user_message
answer
max_steps
steps
error
pending_action
created_at
duration
```

日志保存在：

```text
workspace/.agent_runs/
```

这个目录属于运行时数据，不提交到 Git。

运行日志的作用：

```text
1. 方便调试 Agent 行为
2. 方便分析失败原因
3. 方便做前端历史记录
4. 方便后续扩展 Agent Eval 和链路观测
```

---

## 12. SSE 流式输出

SSE 流式 Agent 位于：

```text
app/agent/agent_stream.py
```

接口：

```text
POST /agent/code/stream
```

它会实时输出 Agent 执行事件：

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

SSE 的价值：

```text
普通接口只能一次性返回最终结果。
SSE 可以实时展示 Agent 正在做什么。
这非常适合前端演示 Coding Agent 的执行过程。
```

---

## 13. Demo Workspace

CodeAgent 不直接操作项目根目录，而是操作：

```text
workspace/
```

其中标准演示项目是：

```text
workspace/demo_project/
```

该目录由脚本生成：

```powershell
python scripts/setup_demo_workspace.py --reset
```

它包含一个独立 Git 仓库，用于演示：

```text
Git status
Git diff
代码读取
代码搜索
文件创建
语法检查
```

主项目不提交 demo_project，原因是：

```text
demo_project 是可再生的演示沙盒
其中包含独立 .git 仓库
不同机器可以通过脚本重建
避免污染主项目 Git 仓库
```

## Self-Reflection Retry

Self-Reflection Retry 是 CodeAgent 的失败诊断与恢复模块。

当工具调用失败时，Agent Loop 会根据工具执行结果生成结构化 reflection。reflection 会被写入当前 step，并在允许重试时作为增强 tool message 反馈给模型，使模型能够在下一轮根据失败原因重新规划。

核心流程：

```text
工具调用
↓
工具执行失败
↓
识别错误类型
↓
生成 reflection
↓
写入 step 日志
↓
判断是否允许 retry
↓
将 reflection 注入 tool message
↓
模型下一轮重新规划
---

## 14. 当前架构亮点

当前项目的主要亮点包括：

```text
1. 原生手写 Agent Loop，而不是直接套壳 LangChain
2. 支持 Tool Calling 工具调用闭环
3. 支持高风险工具审批机制
4. 支持工具风险等级划分
5. 支持 workspace 沙盒路径限制
6. 支持 Git status / Git diff 工具
7. 支持 SSE 实时流式执行过程
8. 支持 Agent 运行日志记录
9. 支持 pytest 单元测试
10. 支持 Agent Eval 简单评估
11. 支持统一开发命令
12. 支持 Demo Workspace 脚本化重建
13. 支持 Docker 部署准备
```

---

## Task Planner

Task Planner 是 CodeAgent 的执行前任务规划模块。

它位于 Agent Loop 之前，负责根据用户输入生成结构化任务计划。当前实现为规则版，不调用大模型。

核心职责：

```text
用户任务
↓
识别任务意图
↓
提取目标路径
↓
推荐工具
↓
估计风险等级
↓
估计任务复杂度
↓
生成执行步骤
↓
生成风险提醒
```

Task Planner 与其他模块的关系：

Task Planner：
执行前规划

Agent Loop：
执行工具调用流程

Reflection：
工具失败或 max_steps 达到上限后的失败自省

Run Logger：
保存 task_plan、steps、reflection 和最终结果

当前 /agent/plan 提供独立规划接口，/agent/code 也会在执行前自动生成 task_plan 并保存到运行日志中。

从 v0.6.0 开始，CodeAgent 的主链路升级为：

Plan → Act → Reflect → Answer

---

## 八、更新 docs/ROADMAP.md

如果你有 Roadmap，可以把 Task Planner 标记为完成。

加入或修改：

```markdown
## 已完成

- v0.4.0：Summary Memory 摘要记忆
- v0.5.0：Self-Reflection Retry 失败自省与重试提示
- v0.6.0：Task Planner 任务规划器

## 下一步

- v0.7.0：Project NoteTool 项目笔记
- v0.8.0：Codebase Retrieval 代码库检索
- v0.9.0：ContextBuilder 上下文工程升级