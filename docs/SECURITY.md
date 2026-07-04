# 安全机制说明

本文档说明 Mini Coding Agent Backend 当前实现的主要安全机制。

由于本项目是一个 Coding Agent，Agent 具备读取代码、搜索代码、运行命令、创建文件和修改文件的能力，因此必须对工具调用进行严格限制。

本项目的安全目标是：

```text
让 Agent 能在受控 workspace 中完成代码任务，
但不能越权访问敏感文件、项目外路径或执行危险命令。
```

---

## 1. 安全设计原则

当前项目遵循以下原则：

```text
1. 默认不信任模型输出
2. 模型只负责规划，后端负责执行
3. 所有工具调用都由后端校验
4. 所有文件路径都限制在 workspace 内
5. 敏感文件默认禁止读取
6. 高风险文件操作必须经过审批
7. 命令执行必须使用白名单
8. 运行日志不能提交到 Git
```

核心思想是：

```text
LLM 不能直接操作系统。
LLM 只能请求工具。
工具是否执行，由后端安全策略决定。
```

---

## 2. Workspace 沙盒限制

CodeAgent 的所有文件类操作都限制在：

```text
workspace/
```

也就是说，用户即使让 Agent 访问：

```text
../.env
C:\Users\xxx\Desktop\secret.txt
```

后端也不应该允许。

项目通过路径解析函数统一处理路径：

```text
safe_resolve_path
```

它的职责是：

```text
1. 将用户传入的相对路径解析成真实绝对路径
2. 检查解析后的路径是否仍然位于 workspace 内
3. 如果路径试图逃逸 workspace，则拒绝访问
```

这样可以防止路径穿越攻击。

示例：

```text
允许：
demo_project/main.py

拒绝：
../.env
../../secret.txt
C:\Users\xxx\.ssh\id_rsa
```

---

## 3. 路径穿越防护

路径穿越是文件工具中最常见的风险之一。

危险输入示例：

```text
../.env
../../README.md
../../../../Windows/System32
```

如果不做限制，Agent 可能读取项目外的敏感文件。

本项目的处理方式是：

```text
所有路径先经过 safe_resolve_path
如果最终绝对路径不在 workspace 内，直接拒绝
```

返回示例：

```text
路径不安全：只能访问 workspace 目录内的文件。
```

---

## 4. 敏感文件保护

当前项目明确禁止读取：

```text
.env
```

原因是 `.env` 中通常包含：

```text
API Key
数据库连接信息
模型服务密钥
私有配置
```

即使 `.env` 位于 workspace 内，也不允许读取。

保护规则：

```text
如果目标文件名是 .env，拒绝读取。
如果路径中包含 .env，拒绝读取。
```

这可以防止 Agent 把密钥暴露给模型。

---

## 5. 隐藏文件和隐藏目录保护

除了 `.env`，项目还默认限制隐藏文件和隐藏目录。

例如：

```text
.git/
.idea/
.vscode/
.pytest_cache/
```

默认不允许 Agent 随意读取这些内容。

原因：

```text
隐藏目录中可能包含 Git 元数据、IDE 配置、缓存文件或其他敏感信息。
```

例外：

```text
.gitignore
```

`.gitignore` 是项目配置文件，需要允许 Agent 读取或维护，所以它是例外。

---

## 6. 文件读取限制

当前文件读取工具包括：

```text
read_file
read_file_lines
search_code
list_files
```

安全限制包括：

```text
1. 只能访问 workspace 内路径
2. 禁止读取 .env
3. 默认忽略隐藏文件和隐藏目录
4. 默认忽略 .bak、.pyc、.log、.tmp 等临时文件
5. 对长文件建议使用 read_file_lines 局部读取
```

这样可以减少：

```text
敏感信息泄露
无关内容污染上下文
超长文件导致 token 浪费
```

---

## 7. 文件修改限制

当前会修改文件的工具包括：

```text
edit_file
write_new_file
ensure_gitignore
```

这些工具被定义为：

```text
high risk
```

高风险工具不会直接执行，而是进入审批流程。

审批流程：

```text
1. Agent 提出高风险工具调用
2. 后端保存 pending action
3. 返回 waiting_approval 状态
4. 用户查看待执行操作
5. 用户批准或拒绝
6. 批准后才真正执行工具
```

这样可以避免模型直接修改文件。

---

## 8. edit_file 的安全设计

`edit_file` 不允许模型随意覆盖整个文件。

它采用：

```text
old_text → new_text
```

的精确替换方式。

也就是说，模型必须先读取文件内容，然后指定：

```text
要替换的旧文本
替换后的新文本
```

如果 `old_text` 在文件中找不到，修改失败。

这个设计可以降低误修改风险。

同时，修改文件前会生成：

```text
.bak
```

备份文件，方便回滚。

---

## 9. write_new_file 的安全设计

`write_new_file` 只允许创建新文件，不允许覆盖已有文件。

如果目标文件已经存在，工具会拒绝执行。

这样可以防止模型误覆盖重要文件。

同时它还限制：

```text
1. 目标路径必须在 workspace 内
2. 不能创建 .env
3. 不能创建隐藏文件，.gitignore 除外
4. 文件内容大小有限制
5. 只允许特定后缀类型
```

---

## 10. 命令执行安全

`run_command` 是中风险工具。

它不会直接执行任意 shell 命令，而是使用：

```text
白名单机制
```

允许的命令通常包括：

```text
python -m py_compile
python --version
git status
git diff
```

不允许的危险命令包括：

```text
rm
del
format
shutdown
pip install
curl
powershell
cmd
```

同时执行命令时使用：

```text
shell=False
```

这可以降低 shell 注入风险。

命令执行还包含：

```text
1. 工作目录限制在 workspace 内
2. 超时限制
3. 返回结构化结果
4. 失败时返回错误类型
```

---

## 11. Git 工具隔离

当前 Git 工具包括：

```text
get_git_status
get_git_diff
```

项目曾经遇到一个真实问题：

```text
当主项目 mini-agent-backend 初始化 Git 后，
workspace 内的普通目录虽然不是 Git 仓库，
但 Git 会自动向上查找父目录 .git，
导致误读主项目 Git 状态。
```

为了解决这个问题，项目新增了判断：

```text
只有目标目录本身包含 .git，
才认为它是 Git 仓库根目录。
```

也就是说：

```text
允许：
workspace/demo_project/.git 存在，因此可以执行 get_git_status。

拒绝：
workspace/test_not_git_project 没有 .git，因此不能误读主项目 Git 状态。
```

这个机制可以防止 Agent 在错误目录下读取 Git 状态。

---

## 12. 工具风险等级

工具风险等级定义在：

```text
app/agent/tool_policy.py
```

当前分为：

```text
low
medium
high
unknown
```

低风险工具：

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

中风险工具：

```text
run_command
```

高风险工具：

```text
edit_file
write_new_file
ensure_gitignore
```

未知工具：

```text
默认视为 unknown，不应被直接信任。
```

---

## 13. 审批机制

高风险工具必须经过审批。

待审批动作会保存到：

```text
workspace/.agent_pending/
```

保存内容包括：

```text
approval_id
agent_name
user_message
tool_name
tool_args
reason
risk_level
created_at
```

用户可以通过接口执行审批：

```text
POST /agent/approvals/{approval_id}/execute
```

请求体：

```json
{
  "approved": true
}
```

或者拒绝：

```json
{
  "approved": false
}
```

拒绝后工具不会执行。

---

## 14. 运行日志隔离

Agent 运行日志保存在：

```text
workspace/.agent_runs/
```

该目录用于调试和历史记录，不提交到 Git。

`.gitignore` 中包含：

```text
workspace/.agent_runs/
workspace/.agent_pending/
```

这样可以避免把本地运行日志、用户输入、工具结果等运行时信息提交到远程仓库。

---

## 15. Git 与 Docker 忽略规则

项目同时维护：

```text
.gitignore
.dockerignore
```

二者职责不同：

```text
.gitignore：
控制哪些文件不提交到 Git。

.dockerignore：
控制哪些文件不打包进 Docker 镜像。
```

两者都会忽略：

```text
.env
.venv
workspace/.agent_runs
workspace/.agent_pending
workspace/demo_project
eval_result.json
临时文件
缓存文件
```

这可以降低：

```text
密钥泄露风险
镜像体积过大
运行时数据污染
本地环境污染
```

---

## 16. 当前安全边界

当前系统已经实现了基础安全机制，但仍然是学习型项目，不应直接用于生产环境。

当前限制：

```text
1. 还没有用户登录和权限系统
2. 还没有多租户隔离
3. 还没有完整审计后台
4. 还没有容器级沙盒隔离
5. run_command 白名单仍需继续完善
6. 文件修改还没有可视化 diff 审批页面
```

---

## 17. 后续安全增强方向

后续可以继续增强：

```text
1. 前端 Diff 审批页面
2. 更细粒度的工具权限控制
3. 用户级 workspace 隔离
4. Docker 容器沙盒执行命令
5. 命令执行资源限制
6. Agent 操作审计后台
7. 敏感信息自动脱敏
8. 工具连续失败熔断机制
9. 高风险操作二次确认
10. Prompt 注入检测
```

---

## 18. 总结

Mini Coding Agent Backend 的安全机制可以概括为：

```text
路径限制：
只能访问 workspace。

文件保护：
禁止读取 .env 和隐藏敏感文件。

工具控制：
模型只能请求工具，不能直接操作系统。

风险分级：
高风险工具必须审批。

命令白名单：
只允许安全命令。

日志隔离：
运行数据不提交到 Git。

Git 隔离：
只允许在独立 workspace Git 仓库中查看状态。
```

当前版本重点是建立一个安全可控的 Coding Agent 原型，为后续扩展前端审批、多用户隔离、容器沙盒和 LLMOps 观测打基础。