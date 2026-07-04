# Changelog

本文档记录 Mini Coding Agent Backend 的重要版本变更。

项目采用语义化版本思想：

```text
MAJOR.MINOR.PATCH
```

当前项目仍处于早期迭代阶段。

---

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