# API 文档

本文档说明 Mini Coding Agent Backend 当前提供的主要接口。

服务默认地址：

```text
http://127.0.0.1:8000
```

启动服务：

```powershell
python scripts/dev.py serve
```

健康检查：

```powershell
python scripts/dev.py health
```

---

## 1. 健康检查接口

### GET /health

用于检查后端服务是否正常运行。

请求：

```powershell
curl.exe http://127.0.0.1:8000/health
```

响应示例：

```json
{
  "status": "ok"
}
```

---

## 2. 普通聊天接口

### POST /chat

用于进行单轮普通 LLM 对话。

请求示例：

```powershell
curl.exe -X POST "http://127.0.0.1:8000/chat" ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"你好，请介绍一下你自己\"}"
```

请求体：

```json
{
  "message": "你好，请介绍一下你自己"
}
```

响应示例：

```json
{
  "answer": "你好，我是一个由后端服务调用的大语言模型助手。"
}
```

说明：

```text
/chat 主要用于测试基础 LLM 调用能力。
它不执行工具，也不进入 CodeAgent 流程。
```

---

## 3. 带历史的聊天接口

### POST /chat/history

用于传入多轮 messages，测试上下文对话能力。

请求体示例：

```json
{
  "messages": [
    {
      "role": "user",
      "content": "我正在做一个后端 Agent 项目。"
    },
    {
      "role": "assistant",
      "content": "好的，你可以继续告诉我项目结构。"
    },
    {
      "role": "user",
      "content": "请总结一下我刚才说的内容。"
    }
  ]
}
```

说明：

```text
该接口用于演示 messages 形式的上下文传递。
当前接口本身不负责长期记忆，长期记忆会在后续版本中扩展。
```

---

## 4. 普通聊天流式接口

### POST /chat/stream

用于测试普通 LLM 的流式输出能力。

请求体示例：

```json
{
  "message": "请用三句话介绍 FastAPI"
}
```

PowerShell 示例：

```powershell
curl.exe -N -X POST "http://127.0.0.1:8000/chat/stream" ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"请用三句话介绍 FastAPI\"}"
```

说明：

```text
该接口用于基础流式输出。
如果要观察 Agent 的工具调用过程，应使用 /agent/code/stream。
```

---

## 5. 时间 Agent 接口

### POST /agent/time

用于演示最简单的工具型 Agent。

请求体示例：

```json
{
  "message": "现在几点？"
}
```

说明：

```text
该接口主要用于早期测试 Agent 调用工具的基础流程。
```

---

## 6. Hello Agent 接口

### POST /agent/hello

用于演示简单 Agent 返回流程。

请求体示例：

```json
{
  "message": "hello"
}
```

说明：

```text
该接口主要用于早期验证 Agent 路由、请求体和响应结构。
```

---

## 7. CodeAgent 主接口

### POST /agent/code

这是当前项目的核心接口。

它支持：

```text
代码文件读取
局部代码读取
代码搜索
Git status
Git diff
运行安全命令
创建新文件
修改已有文件
高风险操作审批
运行日志记录
```

请求体示例：

```json
{
  "message": "请读取 demo_project/main.py 的前 20 行",
  "max_steps": 8
}
```

PowerShell 示例：

```powershell
curl.exe -X POST "http://127.0.0.1:8000/agent/code" ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"请读取 demo_project/main.py 的前 20 行\",\"max_steps\":8}"
```

响应中通常包含：

```text
answer：最终回答
steps：Agent 执行步骤
status：任务状态
run_id：运行日志 ID
pending_action：待审批动作
```

典型状态：

```text
finished：任务完成
failed：任务失败
max_steps_reached：达到最大步骤数
waiting_approval：等待用户审批
rejected：审批被拒绝
```

---

## 8. CodeAgent 流式接口

### POST /agent/code/stream

这是 CodeAgent 的 SSE 流式接口。

它会实时返回 Agent 执行过程，例如：

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

请求体示例：

```json
{
  "message": "请检查 demo_project 的 Git 状态",
  "max_steps": 8
}
```

PowerShell 示例：

```powershell
curl.exe -N -X POST "http://127.0.0.1:8000/agent/code/stream" ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"请检查 demo_project 的 Git 状态\",\"max_steps\":8}"
```

说明：

```text
该接口适合前端演示。
前端可以根据事件类型实时展示 Agent 当前在思考、调用工具、等待审批还是已经完成。
```

---

## 9. Agent 运行记录列表

### GET /agent/runs

用于查看最近的 Agent 运行记录。

请求示例：

```powershell
curl.exe "http://127.0.0.1:8000/agent/runs"
```

说明：

```text
运行记录保存在 workspace/.agent_runs/ 中。
该目录属于运行时数据，不提交到 Git。
```

---

## 10. Agent 单次运行详情

### GET /agent/runs/{run_id}

用于查看某一次 Agent 执行详情。

请求示例：

```powershell
curl.exe "http://127.0.0.1:8000/agent/runs/run_xxxxx"
```

说明：

```text
run_id 可以从 /agent/code、/agent/code/stream 或 /agent/runs 中获取。
```

---

## 11. 审批执行接口

### POST /agent/approvals/{approval_id}/execute

当 Agent 需要执行高风险操作时，例如：

```text
edit_file
write_new_file
ensure_gitignore
```

系统会返回：

```text
status = waiting_approval
pending_action
approval_id
```

用户确认后，可以调用审批接口执行。

请求体示例：

```json
{
  "approved": true
}
```

如果拒绝：

```json
{
  "approved": false
}
```

说明：

```text
审批机制用于防止 Agent 直接修改文件。
高风险工具必须经过用户确认后才能执行。
```

---

## 12. 当前工具风险等级

当前工具大致分为三类：

### 低风险工具

```text
list_files
read_file
read_file_lines
search_code
get_file_diff
get_workspace_diff
get_git_status
get_git_diff
```

特点：

```text
只读操作，不直接修改文件。
```

### 中风险工具

```text
run_command
```

特点：

```text
只允许执行白名单命令，并限制在 workspace 目录内。
```

### 高风险工具

```text
edit_file
write_new_file
ensure_gitignore
```

特点：

```text
会修改或创建文件，必须经过审批。
```

---

## 13. 推荐测试顺序

首次启动后，建议按顺序测试：

```powershell
python scripts/dev.py setup-demo
python scripts/dev.py serve
python scripts/dev.py health
python scripts/dev.py test
python scripts/dev.py eval
```

然后访问：

```text
http://127.0.0.1:8000/docs
```

查看 FastAPI 自动生成的交互式接口文档。