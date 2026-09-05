# Changelog

本文档记录 Mini Coding Agent Backend 的重要版本变更。

项目采用语义化版本思想：

```text
MAJOR.MINOR.PATCH
```

当前项目仍处于早期迭代阶段。

---

## Unreleased — Day21 工程化收尾

- 重构中文 README：真实 v2 架构、安全边界、16 任务 / 12 指标评估与三条 Demo 路径。
- Docker 使用 Python 3.13、Git、非 root 用户、健康检查及 workspace volume；移除构建期 Demo 初始化。
- 完善 .dockerignore，排除整个 workspace、环境文件和缓存。
- 新增 push / pull_request CI：安装依赖、pip check、pytest；不调用真实模型 Eval。
- 补充测试隔离夹具，消除真实 workspace 写入和 MCP 对本地 Demo 的依赖；保留 stdio 集成测试。
- 更新路线图和工程说明。未修改 Agent Runtime、Planner / Executor / Policy 或评估指标。
- 本地 pytest：383 passed；Docker Runtime 不可用，未真实构建镜像。

## 已实现能力补记（Day20 及之前，非本轮新增）

- Planner / Executor 步骤约束、Policy Guard、人工审批、Verified Change / Verification / Rollback、Reflection。
- LangGraph v1 / v2 并行入口、独立 SQLite checkpoint、Context Builder 请求侧压缩。
- 本地只读 MCP Server / Client / Bridge 与独立 Runtime 装配。
- golden_v1.json：16 个任务、12 项指标、文件证据、offline 重评分、compare。
- workspace 路径 canonicalization、不存在 v2 thread resume 返回 404、明确写意图识别。

以下版本条目记录当时行为，不代表当前架构；当前入口与边界以 README 和代码为准。

## v0.6.0 - Task Planner 任务规划器

### 新增功能
- 新增 `app/agent/task_planner.py`，实现规则版任务规划器。
- 支持根据用户任务识别任务意图，包括：
  - read
  - search
  - analyze
  - edit
  - test
  - git
  - plan
- 支持从用户任务中提取目标路径，例如：
  - `demo_project/main.py`
  - `app/agent/agent_loop.py`
  - `tests/test_xxx.py`
- 支持根据任务意图推荐工具，例如：
  - `read_file`
  - `search_code`
  - `edit_file`
  - `run_command`
  - `get_workspace_diff`
  - `get_git_status`
- 支持估计任务风险等级：
  - low：只读任务
  - medium：命令执行任务
  - high：文件修改任务
- 支持估计任务复杂度：
  - simple
  - medium
  - complex
- 支持生成结构化执行计划，包括步骤标题、步骤说明、建议工具、风险等级和规划原因。
- 新增独立规划接口：
  - `POST /agent/plan`
- `/agent/code` 返回结果新增 `task_plan` 字段。
- Agent run log 新增 `task_plan` 字段，用于保存执行前任务规划结果。
- Agent step 新增 `model_round` 字段，用于区分模型调用轮次和执行事件编号。
- 修复一次模型响应包含多个工具调用时 step 编号重复的问题。

### 修改内容
- `AgentStep` 新增 `model_round` 字段。
- Agent 响应 schema 新增 `task_plan` 字段。
- `run_logger.py` 支持保存 `task_plan`。
- `agent_loop.py` 在执行前生成任务规划。
- `/agent/code` 返回结果包含执行前计划、执行步骤和失败自省信息。
- 优化 Task Planner，避免 edit 任务中重复生成读取步骤。

### 新增测试
- `tests/test_task_planner.py`
- `tests/test_agent_plan_api.py`
- `tests/test_agent_code_task_plan.py`
- `tests/test_run_logger_task_plan.py`
- `tests/test_agent_loop_step_numbering.py`

### 说明
- 当前 Task Planner 是规则版，不调用大模型。
- 当前 Task Planner 只负责规划，不直接执行工具。
- `/agent/plan` 是独立规划接口。
- `/agent/code` 会自动生成 `task_plan`，然后进入 Agent Loop 执行。
- 当前 Agent 主链路升级为：Plan → Act → Reflect → Answer。

## v0.5.0 - Self-Reflection Retry 失败自省版本

### 新增功能

- 新增 `app/agent/reflection.py`，集中实现失败分析与重试建议。
- 工具执行失败时生成结构化 `reflection`。
- 支持识别工具错误类型，包括文件不存在、命令失败和路径错误等。
- 支持根据不同错误类型生成：
  - 错误原因分析；
  - 用户可理解的说明；
  - 修复建议；
  - 推荐的下一步工具；
  - 是否允许重试。
- 支持达到 `max_steps` 时生成 reflection。
- 支持用户拒绝审批时生成 reflection。
- 支持模型调用失败时生成 reflection。
- Agent Loop 支持把 reflection 保存到对应执行步骤。
- Agent Loop 支持把 reflection 作为 retry hint 反馈给模型。
- 支持限制 reflection 重试次数，避免无限循环。
- `AgentStep` 新增：
  - `reflection`
  - `retry_from_reflection`

### 修改内容

- 优化工具错误结果识别逻辑。
- 支持识别结构化工具错误。
- 兼容部分旧版字符串形式的工具错误结果。
- Agent 运行日志支持保存 reflection。
- 达到最大执行步数时增加独立 reflection step。
- 工具失败后，模型下一轮可以读取失败原因和修复建议。

### 新增测试

- `tests/test_reflection.py`
- `tests/test_reflection_schema.py`
- `tests/test_agent_loop_reflection.py`
- `tests/test_agent_loop_reflection_retry.py`
- `tests/test_agent_loop_max_steps_reflection.py`

### 说明

- 当前 Self-Reflection 是规则驱动的结构化失败分析，不会额外调用一个专门的反思模型。
- 当前 reflection 最多引导有限次数的重试，避免 Agent 陷入无限循环。
- Reflection 可以提高失败后的可解释性，但其实际恢复效果仍需要通过更完整的 Eval 验证。

---

## v0.4.0 - Summary Memory 摘要记忆基础版本

### 新增功能

- 新增会话摘要字段 `summary`，用于保存长期摘要记忆。
- 新增 summary 兼容逻辑，旧会话会自动补充空 summary。
- 新增 summary 读取和保存接口：
  - `GET /conversations/{conversation_id}/summary`
  - `PUT /conversations/{conversation_id}/summary`
- 新增 `app/memory/summary_manager.py`。
- 支持根据 `summary.source_message_count` 找出未摘要的新消息。
- 支持构造 Summary Memory Prompt。
- 新增自动刷新摘要接口：
  - `POST /conversations/{conversation_id}/summary/refresh`
- `/chat/memory` 支持把 Summary Memory 注入上下文。
- `context_stats` 新增 `summary_used` 字段。
- 新增 Summary Memory 相关单元测试。

### 修改内容

- `ConversationDetailResponse` 新增 `summary` 字段。
- `MemoryChatResponse.context_stats` 新增 `summary_used`。
- 更新 `scripts/check_project.py`，纳入 summary manager 和相关测试。
- 测试数量增加到 49 passed。

### 说明

- 当前 Summary Memory 支持手动更新和接口自动刷新。
- 当前自动摘要仍然是基础版，没有接入后台定时任务。
- 当前还没有实现向量记忆、RAG 和长期数据库存储。
- 本版本为后续长期记忆、上下文压缩和 Agentic RAG 奠定基础。

## v0.3.0 - 上下文管理基础版本

### 新增功能

- 新增 `app/memory/context_manager.py`。
- 新增上下文窗口构建能力。
- 支持对历史消息进行规范化处理。
- 支持按消息数量裁剪历史记录。
- 支持按 token 预算裁剪上下文。
- 新增粗略 token 估算逻辑。
- `/chat/memory` 接入 Context Manager。
- `/chat/memory` 返回 `context_stats`，用于观察上下文使用情况。
- 新增 `tests/test_context_manager.py` 单元测试。

### 修改内容

- `MemoryChatRequest` 新增 `max_context_tokens` 参数。
- `MemoryChatResponse` 新增 `context_stats` 字段。
- 更新 `scripts/check_project.py`，纳入 context manager 文件和测试。
- 测试数量从 33 个增加到 38 个。

### 说明

- 当前 token 估算是粗略估算，不等于真实 tokenizer 计算结果。
- 当前策略是先按消息数量裁剪，再按 token 预算裁剪。
- 本版本为后续 Summary Memory、长期记忆和 RAG 提供上下文管理基础。


## v0.2.0 - 会话记忆基础版本

### 新增功能

- 新增本地 JSON 会话存储模块，运行时数据保存在 `workspace/.conversations/`。
- 新增 `conversation_id` 机制，用于区分不同会话。
- 支持创建会话、读取会话、列出会话、追加消息。
- 新增会话管理接口：
  - `POST /conversations`：创建新会话
  - `GET /conversations`：查看最近会话列表
  - `GET /conversations/{conversation_id}`：查看某个会话详情
  - `POST /conversations/{conversation_id}/messages`：向会话追加消息
- 新增带记忆的聊天接口：
  - `POST /chat/memory`
- `/chat/memory` 支持：
  - 不传 `conversation_id` 时自动创建新会话
  - 传入 `conversation_id` 时继续已有会话
  - 自动保存用户消息和助手回复
  - 自动读取最近历史消息并传给大模型
- 新增简单滑动窗口机制，用于限制传给大模型的历史消息数量。
- 新增会话存储模块单元测试。
- 新增 `/chat/memory` 接口自动化测试。
- 测试数量从 23 个增加到 33 个。

### 修改内容

- 更新 `scripts/check_project.py`，把 memory 模块和新增测试纳入项目自检。
- 更新 `.gitignore`，忽略 `workspace/.conversations/` 运行时会话数据。
- 更新 `.dockerignore`，避免把本地会话数据打包进 Docker 镜像。

### 说明

- 当前版本使用本地 JSON 文件保存会话，方便学习、调试和查看数据结构。
- 当前还没有使用 Redis、SQLite、PostgreSQL 等数据库。
- 当前还没有实现摘要记忆、长期记忆、向量记忆和 RAG。
- 本版本是后续上下文压缩、Summary Memory、长期记忆系统的基础。

## v0.1.0 - 基础工程化版本

### 版本定位

v0.1.0 是 Mini Coding Agent Backend 的第一个阶段性版本。

该版本目标不是实现最终形态的 Coding Agent，而是完成一个可运行、可测试、可评估、可提交、可继续迭代的基础后端 Agent 系统。

---

### 已完成功能

#### 1. FastAPI 后端基础能力

```text
健康检查接口
普通聊天接口
带历史消息的聊天接口
普通 LLM 流式输出接口
CodeAgent 主接口
CodeAgent SSE 流式接口
Agent 运行记录接口
高风险操作审批接口
```

#### 2. 原生手写 CodeAgent

```text
原生 Agent Loop
Tool Calling 工具调用
多步工具执行
工具结果回填
最大步骤限制
结构化 Agent Step
状态管理
错误类型记录
```

#### 3. 文件与代码工具系统

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

#### 4. 安全机制

```text
workspace 沙盒限制
safe_resolve_path 路径安全检查
禁止读取 .env
隐藏文件保护
高风险工具审批
命令执行白名单
shell=False
工具风险等级
Git 仓库根目录隔离
运行日志忽略提交
```

#### 5. 审批机制

```text
高风险工具进入 waiting_approval
pending action 本地保存
审批后执行工具
审批拒绝后终止操作
审批执行后 Agent 可继续总结结果
```

#### 6. SSE 流式执行过程

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

#### 7. 测试与评估

```text
pytest 单元测试
file_tools 测试
git_tools 测试
approval_store 测试
run_logger 测试
Agent Eval 轻量评估集
run_eval.py 自动评估脚本
```

当前测试状态：

```text
pytest：23 passed
Agent Eval：10/10 passed
```

#### 8. 工程化脚本

```text
scripts/dev.py
scripts/check_project.py
scripts/check_release.py
scripts/setup_demo_workspace.py
```

支持命令：

```powershell
python scripts/dev.py serve
python scripts/dev.py health
python scripts/dev.py test
python scripts/dev.py eval
python scripts/dev.py check
python scripts/dev.py setup-demo
python scripts/dev.py release-check
```

#### 9. 文档体系

```text
README.md
docs/API.md
docs/ARCHITECTURE.md
docs/SECURITY.md
docs/EVALUATION.md
docs/ROADMAP.md
CHANGELOG.md
```

#### 10. 部署准备

```text
Dockerfile
.dockerignore
.env.example
requirements.txt
```

---

### 重要修复

#### 1. 修复 requirements.txt 编码问题

处理了 UTF-16 空字符和 UTF-8 BOM 导致依赖识别失败的问题。

#### 2. 修复 Windows 删除 demo_project/.git 权限问题

增强 `setup_demo_workspace.py` 的删除逻辑，支持只读文件处理和重试机制。

#### 3. 修复 Git 工具误读主项目 Git 状态的问题

要求 `get_git_status` 和 `get_git_diff` 只能作用于目标目录本身包含 `.git` 的独立仓库根目录，避免 workspace 普通目录误读父级 Git 仓库状态。

#### 4. 修复发布检查脚本密钥误报问题

优化 `check_release.py` 的密钥扫描逻辑，避免扫描脚本自身和测试占位符造成误报。

---

### 当前限制

```text
尚未实现会话持久化记忆
尚未实现上下文压缩
尚未实现 Self-Reflection 失败自省
尚未实现代码结构索引
尚未接入向量检索
尚未实现前端演示页面
尚未实现 LangGraph 状态机版本
尚未接入 LangSmith
尚未接入 SWE-bench Lite
```

---

### 下一阶段计划

v0.2.0 计划重点：

```text
新增 conversation_id
实现本地 JSON 会话持久化
支持多轮对话历史读取
支持 messages 滑动窗口截断
实现摘要压缩
为后续长任务 Agent 打基础
```

详细路线见：

```text
docs/ROADMAP.md
```