# 项目路线图

本文档记录 Mini Coding Agent Backend 的当前完成状态、主要限制和后续求职导向的迭代路线。

项目采用阶段性开发方式。每一个版本不仅增加功能，还需要同步完成测试、评估、文档和可复现性验证。

---

## 1. 项目定位

Mini Coding Agent Backend 是一个后端优先的仓库级代码智能体项目。

项目目标不是实现一个简单的聊天机器人，也不是单纯堆叠 Agent 框架，而是逐步构建一个：

```text
可理解代码仓库
可规划代码任务
可调用受控工具
可安全修改代码
可运行测试验证
可分析失败原因
可人工审批高风险操作
可记录完整执行轨迹
可通过固定评估集验证效果
```

的可靠 Coding Agent。

长期定位：

```text
Reliable Repository-Level Coding Agent
可审计、可评估、可恢复的仓库级代码智能体
```

---

## 2. 当前版本：v0.6.0

当前版本已经完成从普通聊天接口到基础 Coding Agent 后端的主要链路。

### 2.1 已完成功能

```text
FastAPI 后端接口
DeepSeek / OpenAI-compatible 模型调用
原生手写 Agent Loop
Tool Calling 多轮工具调用
代码和文件读取
局部代码读取
关键词代码搜索
文件创建与精确修改
安全命令执行
Git status 和 Git diff
Workspace 路径沙箱
工具风险分级
高风险操作人工审批
Approval Resume 基础续跑
Trace 执行轨迹
运行日志持久化
历史运行查询
SSE 流式执行过程
Conversation Memory
Context Manager
Summary Memory
Self-Reflection Retry
规则版 Task Planner
pytest 自动化测试
Agent Eval
工程自检脚本
统一开发命令
Demo Workspace 初始化
Docker 部署准备
项目文档体系
```

### 2.2 当前主链路

```text
用户任务
↓
Task Planner 生成结构化任务分析
↓
Agent Loop 调用模型
↓
模型选择工具
↓
后端执行工具或进入人工审批
↓
工具失败时生成 Reflection
↓
模型根据失败提示重新规划
↓
生成最终回答
↓
保存 Task Plan、Steps、Reflection 和运行结果
```

### 2.3 当前必须准确说明的限制

当前 Task Planner 会生成并保存结构化计划，但计划尚未直接控制工具执行顺序。

因此当前 Planner 更准确地属于：

```text
执行前任务分析与可观测性模块
```

还不是完整的 Planner-Executor 架构。

普通 Agent 接口和 SSE 流式接口目前存在两套执行循环，部分能力没有完全保持一致。

当前代码搜索主要是关键词搜索，还不具备完整的仓库结构理解、依赖分析和语义检索能力。

当前 Eval 主要验证工具选择、状态和安全策略，还没有充分验证复杂代码修复的最终正确性。

当前会话和运行状态主要使用本地 JSON 文件保存，还不适合并发和生产环境。

---

## 3. v0.6.1：可靠性基线与文档对齐

目标是修复当前版本中的工程问题，为后续能力升级建立稳定基线。

计划完成：

```text
修复审批记录不存在时的空值错误
补充审批接口回归测试
统一 VERSION、README、CHANGELOG 和 ROADMAP
建立版本化 Eval 结果记录
记录模型、代码提交、任务集和运行日期
增加 .gitattributes，统一跨平台换行符
清理分享压缩包中的敏感文件和本地环境
优化 LLM Client 初始化方式
使无 API Key 的纯单元测试可以正常运行
```

完成标准：

```text
全部 pytest 通过
工程自检通过
发布前检查通过
真实 Agent Eval 通过
文档描述与代码行为一致
```

---

## 4. v0.7.0：统一 Agent Engine 与可观测性升级

目标是消除普通接口和流式接口之间的执行逻辑重复。

计划完成：

```text
抽象统一 Agent Engine
普通接口和 SSE 接口复用相同执行事件
统一 Planner、Tool Call、Approval、Reflection 和 Final Answer 流程
记录模型调用耗时
记录工具执行耗时
记录模型轮数
记录 Token 使用或估算
记录重试次数
记录总任务耗时
统一 Trace Event 数据结构
```

重点学习：

```text
生成器
事件驱动设计
SSE
依赖注入
可观测性
Trace 和 Metrics
```

---

## 5. v0.8.0：仓库级代码理解与混合检索

目标是让 Agent 从关键词搜索升级为仓库级代码理解。

计划完成：

```text
扫描 Workspace 中的 Python 文件
使用 AST 提取类、函数、方法和 import
建立代码符号索引
记录文件与符号之间的关系
支持按文件名、函数名和类名检索
增加 BM25 或关键词检索
增加 Embedding 语义检索
实现关键词与语义混合检索
增加结果重排
实现增量索引更新
为检索结果附加来源和相关性说明
```

建议模块：

```text
app/retrieval/
├── code_parser.py
├── symbol_index.py
├── lexical_search.py
├── semantic_search.py
├── hybrid_retriever.py
└── index_store.py
```

重点学习：

```text
Python AST
Embedding
Chunking
BM25
向量相似度
混合检索
Reranking
Context Engineering
```

---

## 6. v0.9.0：可靠代码修改闭环

目标是让 Agent 不只是提出修改，而是能够验证修改是否正确。

计划完成：

```text
基于 Patch 的代码修改
修改前创建 Git 分支或 Worktree
限制一次任务可修改的文件数量
修改后自动查看 Git diff
自动运行相关测试
测试失败后提取错误信息
根据错误生成有限次数的修复建议
修复失败时自动回滚
测试通过后生成变更报告
人工审批最终补丁
```

目标流程：

```text
理解任务
↓
检索相关代码
↓
生成修改计划
↓
人工确认
↓
隔离环境修改代码
↓
运行测试
↓
失败分析与有限重试
↓
测试通过
↓
展示最终 Diff
↓
生成变更报告
```

重点学习：

```text
Git Branch
Git Worktree
Patch
自动化测试
回滚
幂等性
错误恢复
```

---

## 7. v0.10.0：Agent Eval 2.0

目标是从基础工具行为测试升级为代码任务结果评估。

计划完成：

```text
将 Eval 扩展到 50 个以上任务
按照能力对任务分类
增加多次重复运行
统计平均值和波动
增加代码修改任务
增加测试驱动修复任务
增加跨文件任务
增加安全攻击任务
增加 Prompt Injection 测试
增加审批绕过测试
增加路径越权测试
增加 Reflection 恢复任务
增加 Planner 对比实验
记录 Token、耗时和调用轮数
生成自动化评估报告
```

核心指标：

```text
任务完成率
最终测试通过率
工具选择正确率
文件定位准确率
危险操作拦截率
失败恢复成功率
平均模型轮数
平均工具调用数
平均 Token 使用量
平均任务耗时
```

后续可以逐步参考 SWE-bench 的任务组织方式，构建本地 Mini SWE Eval。

---

## 8. v0.11.0：持久化、状态恢复与框架对比

目标是提升 Agent 长任务和多任务运行能力。

计划完成：

```text
使用 SQLite 保存基础状态
逐步迁移到 PostgreSQL
使用 ORM 管理数据
保存 Agent Run 状态
保存审批状态
支持任务中断与恢复
支持失败后重新加载状态
支持并发任务隔离
保留原生 Agent Loop
新增 LangGraph 对照版本
对比手写循环与状态机的优缺点
```

重点学习：

```text
SQL
SQLite
PostgreSQL
SQLAlchemy
事务
状态持久化
LangGraph
Human-in-the-loop 状态恢复
```

---

## 9. v0.12.0：MCP 工具标准化

目标是学习并实现标准化 Agent 工具连接方式。

计划完成：

```text
理解 MCP Client 和 MCP Server
将部分只读工具暴露为 MCP Server
将 Git 工具暴露为 MCP Server
支持工具发现
支持 MCP 工具权限配置
支持 MCP 调用超时
记录 MCP 调用 Trace
允许 Agent 连接外部 MCP Server
```

MCP 不会替代现有 Tool Calling，而是作为标准化工具接口的扩展。

---

## 10. v1.0.0：求职展示版本

v1.0.0 的目标是形成一个可运行、可演示、可评估、可解释的核心求职项目。

预期能力：

```text
可靠的仓库级代码检索
结构化任务规划
安全工具系统
高风险人工审批
隔离代码修改
自动测试验证
失败分析和有限恢复
完整 Trace 和指标
系统化 Agent Eval
任务状态持久化
可视化前端
Docker Compose 部署
GitHub Actions 持续集成
完整 README 和架构文档
完整演示视频和面试材料
```

前端需要能够展示：

```text
任务输入
实时执行计划
模型和工具步骤
审批弹窗
Reflection
代码 Diff
测试结果
耗时和 Token
历史任务
Eval Dashboard
```

v1.0.0 不代表达到成熟商业 Coding Agent 的能力，而是表示该项目已经能够作为求职中的核心工程项目进行展示和深入讲解。

---

## 11. 项目开发原则

后续迭代遵循以下原则：

```text
先保证正确，再增加功能
先把单 Agent 做稳定，再考虑多 Agent
先理解底层，再使用框架
每个功能必须有测试
重要功能必须有 Eval
高风险操作必须可审计
文档描述必须与代码行为一致
指标必须可以复现
失败必须可解释
```

---

## 12. 长期可选方向

在 v1.0.0 之后，可以继续探索：

```text
多 Agent 协作
GitHub Issue 自动处理
Pull Request 自动生成
Reviewer Agent
多模型路由
成本优化
后台任务队列
分布式执行
私有模型部署
SWE-bench 子集评估
Agentic RAG
Deep Research Agent
```

这些功能只有在核心单 Agent 系统稳定后才考虑加入。
