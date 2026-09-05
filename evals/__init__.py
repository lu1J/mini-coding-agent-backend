"""Day19 Agent Eval（Agent 评测）框架。

分层：
- dataset.py  固定标准评测集（Golden Dataset）加载与校验、fixture 路径；
- scoring.py  纯函数确定性评分器（输入 task + trace + evidence）；
- runner.py   真实任务执行（HTTP + 审批 resume + evidence 取证）；
- report.py   汇总 / 终端报告 / 结果归档 / offline 重评分；
- compare.py  两个 eval run 的结果对比（回归检测）。
"""

from __future__ import annotations

EVALS_FRAMEWORK_VERSION = "1.0.0"
