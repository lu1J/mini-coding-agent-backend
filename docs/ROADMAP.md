# 项目路线图

本文档说明 Mini Coding Agent Backend 的当前完成状态和后续迭代计划。

本项目采用阶段性迭代方式开发。当前版本不是最终版本，而是一个已经具备基础 Agent Loop、工具调用、安全控制、日志、评估和工程化脚本的 v0.1.0 基础版本。

---

## 1. 当前项目定位

Mini Coding Agent Backend 是一个后端优先的 Coding Agent 原型系统。

项目目标不是做一个简单聊天机器人，而是逐步实现一个具备以下能力的代码智能体：

```text
能理解用户代码任务
能读取和搜索代码
能调用后端工具
能执行安全命令
能查看 Git 状态和 diff
能创建和修改文件
能进行高风险操作审批
能记录执行轨迹
能通过固定任务集评估稳定性
能逐步扩展记忆、自省、检索和前端演示能力
```

---

## 2. 当前版本：v0.1.0 基础工程化版本

当前阶段的重点是：

```text
打通原生手写 Agent Loop
建立工具调用闭环
建立安全边界
建立运行日志
建立测试和评估体系
完成基础工程化包装
```

当前已经完成：

```text
FastAPI 后端接口
DeepSeek / OpenAI SDK Compatible API 调用
普通聊天接口
普通流式输出接口
CodeAgent 主接口
CodeAgent SSE 流式接口
原生手写 Agent Loop
Tool Calling 工具调用
文件读取工具
局部代码读取工具
代码搜索工具
文件创建工具
文件修改工具
安全命令执行工具
Git status / Git diff 工具
工具风险等级
高风险操作审批
运行日志记录
Agent Eval 评估脚本
pytest 单元测试
统一开发命令
Demo Workspace 初始化脚本
发布前安全检查脚本
Docker 部署准备
API 文档
架构说明文档
安全机制文档
评估说明文档
```

当前版本的目标不是功能最终形态，而是建立一个稳定、可复现、可继续迭代的基础版本。

---

## 3. v0.2.0：会话记忆与上下文压缩

下一阶段计划增加会话记忆能力。

目标：

```text
支持 conversation_id
支持本地 JSON 会话持久化
支持多轮对话历史读取
支持 messages 滑动窗口截断
支持超长上下文摘要压缩
支持按会话查看历史记录
```

计划新增模块：

```text
app/memory/
├── conversation_store.py
├── context_manager.py
└── summarizer.py
```

重点解决的问题：

```text
当前 Agent 每次请求主要依赖当前输入。
后续需要支持跨轮任务、历史上下文复用和长对话压缩。
```

完成后，项目可以支持更真实的多轮 Coding Agent 场景。

---

## 4. v0.3.0：Self-Reflection 失败自省循环

该阶段计划增强 Agent 的自主纠错能力。

目标：

```text
工具执行失败后自动分析原因
代码运行失败后自动复盘错误
生成 reflection prompt
重新规划下一步工具调用
限制最大反思次数，避免无限循环
记录 reflection steps
```

典型流程：

```text
Tool Error
   ↓
Reflection Prompt
   ↓
Analyze Failure
   ↓
Re-plan
   ↓
Retry Tool
   ↓
Finish or Fail Gracefully
```

重点解决的问题：

```text
当前 Agent 遇到工具失败时，主要依赖模型下一轮自然处理。
后续希望显式加入失败自省逻辑，让 Agent 更像真正的闭环工程系统。
```

该阶段是项目的重要加分点。

---

## 5. v0.4.0：代码结构索引与检索增强

该阶段计划增强 CodeAgent 在大型项目中的代码定位能力。

第一步不直接上复杂向量库，而是先做代码结构索引。

目标：

```text
扫描 workspace 中的 Python 文件
提取文件路径、类名、函数名、docstring、import 信息
建立本地代码索引
支持按函数名、类名、关键词检索
支持搜索结果排序
支持检索结果再局部读取
```

计划新增模块：

```text
app/index/
├── code_indexer.py
├── code_searcher.py
└── index_store.py
```

后续再考虑接入：

```text
FAISS
Chroma
Embedding-based semantic search
Hybrid search
```

重点解决的问题：

```text
当前 search_code 更偏关键词搜索。
后续需要适配更大的代码仓库，降低 token 消耗，提高定位准确率。
```

---

## 6. v0.5.0：极简前端演示页面

该阶段计划增加一个轻量前端，方便项目展示。

优先考虑：

```text
Streamlit
```

而不是复杂 Vue / React。

目标页面功能：

```text
输入任务
发送到 /agent/code/stream
实时展示 Agent 执行事件
展示工具调用步骤
展示最终回答
展示 pending approval
支持点击审批
展示运行历史记录
```

重点解决的问题：

```text
当前项目主要通过 curl 和 FastAPI docs 演示。
后续需要一个更直观的页面，方便面试和项目展示。
```

---

## 7. v0.6.0：LangGraph 状态机版本

该阶段计划用 LangGraph 重构一个并行版本，而不是替换原生版本。

目标：

```text
保留原生手写 Agent Loop
新增 LangGraph 版本 CodeAgent
对比原生循环和状态机编排
实现节点、边、条件分支和循环
支持 human-in-the-loop 审批节点
```

计划新增：

```text
app/agent/langgraph_code_agent.py
```

重点解决的问题：

```text
原生 Agent Loop 更适合理解底层机制。
LangGraph 更适合表达复杂状态流和多阶段任务编排。
```

该阶段可以作为面试时解释框架理解能力的亮点。

---

## 8. v0.7.0：Agent Eval 升级

该阶段计划升级评估体系。

目标：

```text
将 eval_tasks.json 扩展到 30 到 50 个任务
按能力分类统计成功率
统计工具调用成功率
统计平均耗时
统计失败原因
记录 token 估算
增加复杂多步骤任务
增加代码修改后测试验证
构建 mini-SWE-bench 本地任务集
```

重点解决的问题：

```text
当前 Eval 是轻量功能验证。
后续希望升级为更系统的 Agent 行为评估。
```

后续可选方向：

```text
LangSmith trace
SWE-bench Lite subset
A/B prompt testing
```

---

## 9. v0.8.0：多 Agent 与任务分工

该阶段计划探索多智能体协作。

可能拆分角色：

```text
Planner Agent：负责规划任务
Reader Agent：负责读取和搜索代码
Editor Agent：负责生成修改方案
Tester Agent：负责运行测试
Reviewer Agent：负责检查风险和总结
```

重点目标：

```text
不是为了堆多 Agent 概念，
而是研究复杂代码任务中角色分工是否能提升稳定性。
```

该阶段会在单 Agent 足够稳定后再做。

---

## 10. v1.0.0：可展示版本

v1.0.0 的目标是形成一个可以正式展示的 Coding Agent 项目。

预期能力：

```text
后端接口完整
Agent Loop 稳定
工具系统清晰
审批机制可用
前端演示可用
基础记忆可用
失败自省可用
代码检索增强
文档完整
测试和 Eval 稳定
Docker 可运行
GitHub README 清晰
简历描述明确
```

v1.0.0 不代表达到 Claude Code 级别，而是代表项目作为简历核心项目已经完整可展示。

---

## 11. 长期方向

长期可以继续探索：

```text
Agentic RAG 智能知识库系统
Deep Research 深度研究智能体
常驻 Agent
事件驱动 Agent
定时任务 Agent
LangSmith 链路观测
OpenClaw 架构学习
SWE-bench Lite 评测
多模型对比
私有化小模型部署
```

这些方向不会在当前版本一次性完成，而是作为后续学习和项目扩展方向。

---

## 12. 总结

当前项目仍在持续迭代中。

当前 v0.1.0 的价值在于：

```text
先完成一个稳定、可运行、可测试、可评估、可提交、可解释的原生 Coding Agent 后端基础版本。
```

后续版本会继续增强：

```text
记忆
自省
检索
前端
状态机
多 Agent
评估
部署
```

项目采用渐进式工程化路线：

```text
先做深一个核心项目，
再逐步扩展框架、评测和工业化能力。
```