# Agent Eval 评估说明

> Day21 文档状态：当前入口为 evals/，golden_v1.json 含 16 个任务、12 项指标，支持 Evidence、offline 重评分与 compare。命令见 [README](../README.md#agent-eval-运行)。下文仅为 legacy run_eval.py 历史说明与当时结果，不代表当前数据集或稳定成功率。


本文档说明 Mini Coding Agent Backend 当前的 Agent Eval 评估设计。

Agent Eval 的目标是验证 CodeAgent 是否能够稳定完成一组标准任务，而不是只凭单次对话感觉判断效果。

---

## 1. 为什么需要 Agent Eval

Coding Agent 与普通聊天机器人不同。

普通聊天机器人主要关注：

```text
回答是否自然
内容是否有用
表达是否流畅
```

Coding Agent 还需要关注：

```text
是否正确调用工具
是否能读取目标文件
是否能搜索代码
是否能查看 Git 状态
是否能执行安全命令
是否能识别高风险操作
是否能进入审批流程
是否能在最大步数内完成任务
```

因此，Agent 需要一组固定任务来重复测试。

---

## 2. 当前评估文件

当前评估相关文件包括：

```text
eval_tasks.json
run_eval.py
eval_result.json
```

说明：

```text
eval_tasks.json：
手工设计的 Agent 评估任务集，可以提交到 Git。

run_eval.py：
评估执行脚本，会逐条调用 /agent/code 接口并统计结果，可以提交到 Git。

eval_result.json：
每次运行后生成的评估结果，属于本地运行产物，不提交到 Git。
```

---

## 3. 当前评估运行方式

运行评估前，需要先启动后端服务：

```powershell
python scripts/dev.py serve
```

然后另开一个终端执行：

```powershell
python scripts/dev.py eval
```

或者直接运行：

```powershell
python run_eval.py
```

---

## 4. 当前评估结果

当前一次评估结果为：

```text
总任务数：10
通过：10
失败：0
成功率：100.0%
平均耗时：7013 ms
平均工具调用数：1.60
```

说明：

```text
当前评估集下，CodeAgent 可以稳定完成基础代码读取、搜索、Git 状态查看、命令执行和审批触发等任务。
```

---

## 5. 当前评估覆盖能力

当前 Eval 主要覆盖以下能力：

```text
1. 文件读取能力
2. 局部代码读取能力
3. 代码搜索能力
4. Git status 能力
5. Git diff 能力
6. Python 语法检查能力
7. 高风险文件创建审批能力
8. 工具调用步骤记录能力
9. Agent 最终回答能力
10. 最大步骤限制下的任务完成能力
```

这些能力对应 CodeAgent 的核心后端功能。

---

## 6. 当前评估指标

当前 Eval 统计的主要指标包括：

```text
任务总数
通过数量
失败数量
成功率
平均耗时
平均工具调用数
单个任务执行状态
单个任务执行步骤
```

其中最重要的是：

```text
成功率：
衡量 Agent 是否能完成任务。

平均耗时：
衡量 Agent 响应效率。

平均工具调用数：
衡量 Agent 是否能用较少步骤完成任务。
```

---

## 7. 为什么不是只看最终回答

Agent 评估不能只看最终回答。

原因是：

```text
模型可能回答得像是完成了任务，但实际上没有调用工具。
模型可能编造文件内容。
模型可能没有真正读取 Git 状态。
模型可能绕过审批机制。
```

因此，本项目的评估不仅关注最终 answer，也关注：

```text
status
steps
tool_name
tool_result
pending_action
risk_level
```

这可以更真实地判断 Agent 是否完成了任务。

---

## 8. 当前评估任务类型

当前任务主要分为以下几类：

### 文件读取任务

验证 Agent 是否能正确读取 workspace 中的文件。

```text
示例：
读取 demo_project/main.py 的前 20 行。
```

### 代码搜索任务

验证 Agent 是否能先搜索，再读取相关上下文。

```text
示例：
搜索 hello 函数相关代码。
```

### Git 工具任务

验证 Agent 是否能读取 demo_project 的 Git 状态和 diff。

```text
示例：
检查 demo_project 的 Git 状态。
```

### 命令执行任务

验证 Agent 是否能通过安全命令检查 Python 文件语法。

```text
示例：
运行 python -m py_compile 检查 math_utils.py。
```

### 审批任务

验证高风险工具不会直接执行，而是进入 waiting_approval。

```text
示例：
创建一个新文件时，应该触发审批。
```

---

## 9. 当前 Eval 的局限

当前评估集仍然是轻量版本。

主要局限：

```text
1. 任务数量较少，只有 10 个
2. 主要覆盖基础工具能力
3. 暂未覆盖复杂多文件修改
4. 暂未覆盖失败自省循环
5. 暂未覆盖长上下文压缩
6. 暂未接入标准评测集
7. 暂未进行多模型对比
8. 暂未统计 token 消耗
```

因此当前 Eval 更适合作为：

```text
项目基础回归测试
功能稳定性检查
演示前自检
```

---

## 10. 后续评估升级方向

后续可以将 Eval 升级为更完整的 Agent 评测系统。

计划方向：

```text
1. 扩充评估任务到 30 到 50 个
2. 按能力分类统计成功率
3. 增加失败原因分类
4. 增加 token 消耗估算
5. 增加工具调用成功率统计
6. 增加多轮任务评估
7. 增加代码修改后 pytest 验证
8. 增加 mini-SWE-bench 本地任务集
9. 接入 LangSmith 做链路观测
10. 后续尝试 SWE-bench Lite 子集
```

---

## 11. 与 pytest 的区别

本项目同时使用：

```text
pytest
Agent Eval
```

二者区别如下：

```text
pytest：
测试后端函数本身是否正确，例如 file_tools、approval_store、run_logger。

Agent Eval：
测试 Agent 是否能在大模型参与下完成真实任务。
```

简单来说：

```text
pytest 测代码。
Agent Eval 测 Agent 行为。
```

两者都重要。

---

## 12. 推荐评估流程

每次重要修改后，建议按顺序执行：

```powershell
python scripts/dev.py test
python scripts/dev.py check
python scripts/dev.py serve
python scripts/dev.py eval
```

如果 Eval 出现失败，应检查：

```text
1. 后端服务是否启动
2. demo_project 是否初始化
3. 失败任务的 status
4. 失败任务的 steps
5. 是否是模型随机性导致
6. 是否是工具逻辑回归
7. 是否是 prompt 约束不足
```

---

## 13. 总结

Mini Coding Agent Backend 当前已经具备轻量级 Agent Eval 能力。

它的价值在于：

```text
不是凭感觉判断 Agent 是否好用，
而是用固定任务集持续验证 Agent 的核心能力。
```

当前 Eval 可以验证：

```text
工具调用是否正确
审批机制是否生效
Git 工具是否隔离
命令执行是否安全
Agent 是否能在限定步骤内完成任务
```

后续可以继续向更标准的 AgentBench、LangSmith、SWE-bench Lite 等方向扩展。
