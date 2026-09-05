"""Day20：Planner / Executor 工具名与真实工具目录的一致性防漂移测试。

背景（Day19 Agent Eval）：Planner 输出的 suggested_tool/allowed_tools 如果
引用了模型目录中不存在的工具名，工具调用会被逐层 Gate 拦下，导致任务失败。

本测试对「Golden Dataset 全部任务 + 代表性意图」逐一运行确定性 Planner，
收集 planner 可能输出的全部工具名，断言它们 ⊆ 真实 CODE_AGENT_TOOLS 目录；
Executor 的等价别名表与低风险辅助表也必须全部指向真实工具。
"""

import pytest

from app.agent.code_agent import CODE_AGENT_TOOLS
from app.agent.execution_policy_guard import extract_planned_tools
from app.agent.plan_executor import (
    SUPPORTING_LOW_RISK_TOOLS,
    TOOL_EQUIVALENTS,
    _allowed_tools_for_step,
)
from app.agent.task_planner import build_task_plan
from evals.dataset import load_golden_tasks


def _real_tool_names() -> set[str]:
    return {
        tool["function"]["name"]
        for tool in CODE_AGENT_TOOLS
        if tool.get("function", {}).get("name")
    }


REAL_TOOL_NAMES = _real_tool_names()


def _tool_names_from_plan(task_plan: dict) -> set[str]:
    """收集一个 plan 中 Planner 可能要求模型调用的全部工具名。"""
    names: set[str] = set()

    for tool_name in extract_planned_tools(task_plan):
        if tool_name:
            names.add(tool_name)

    for step in task_plan.get("steps", []) or []:
        suggested = (step or {}).get("suggested_tool")
        if suggested:
            names.add(suggested)
            names.update(_allowed_tools_for_step(suggested))

    return names


# 覆盖 read / search / structure / dependency / impact / edit / write /
# diff / git / verify / approval 等代表性意图。
REPRESENTATIVE_INTENTS = [
    "读取 demo_project/main.py 的内容，并总结它做了什么。",
    "在 demo_project 里搜索 print 调用，列出所有出现位置。",
    "列出 demo_project 目录结构。",
    "获取 demo_project/api.py 的函数大纲。",
    "分析 demo_project/api.py 导入了哪些本地模块。",
    "分析 demo_project/models.py 被哪些文件依赖。",
    "修改 demo_project/math_utils.py 中 multiply 的 docstring 并验证。",
    "在 demo_project 新建 utils.py，实现 add 函数，并运行测试。",
    "看看 demo_project 最近改了什么，git 状态如何。",
    "运行 demo_project 的 pytest 测试并报告结果。",
    "把 workspace/demo_project/config.py 里的 DEBUG 改为 False。",
    "修复 demo_project/tests 里失败的用例，先读代码再修改再验证。",
]


def test_real_tool_catalog_is_not_empty():
    assert len(REAL_TOOL_NAMES) >= 15
    assert {"read_file", "read_file_lines", "edit_file", "write_new_file",
            "run_command"} <= REAL_TOOL_NAMES


@pytest.mark.parametrize("prompt", REPRESENTATIVE_INTENTS)
def test_planner_tools_exist_in_real_catalog_for_intents(prompt):
    plan = build_task_plan(prompt)
    emitted = _tool_names_from_plan(plan)

    assert emitted, f"该意图没有产出任何计划工具：{prompt}"
    unknown = emitted - REAL_TOOL_NAMES
    assert not unknown, f"Planner 输出了不存在的工具：{unknown}"


def test_planner_tools_exist_in_real_catalog_for_golden_tasks():
    """Golden Dataset 全部 16 个任务（含真实 eval 路径表述）都必须
    只推荐真实存在的工具。"""
    for task in load_golden_tasks():
        prompt = task["prompt_template"].format(
            case_root=(
                "workspace/eval_cases/run_1/"
                f"{task['id']}/demo_project"
            )
        )
        plan = build_task_plan(prompt)
        emitted = _tool_names_from_plan(plan)

        assert emitted, f"{task['id']} 没有任何计划工具"
        unknown = emitted - REAL_TOOL_NAMES
        assert not unknown, f"{task['id']} 输出不存在的工具：{unknown}"


def test_executor_alias_and_supporting_tables_only_reference_real_tools():
    """Executor 的等价别名与低风险辅助表如果引入拼写漂移，同样会导致
    计划步骤无法由真实工具完成。"""
    referenced = set(TOOL_EQUIVALENTS) | set(SUPPORTING_LOW_RISK_TOOLS)
    for values in TOOL_EQUIVALENTS.values():
        referenced |= set(values)

    unknown = referenced - REAL_TOOL_NAMES
    assert not unknown, f"Executor 表引用了不存在的工具：{unknown}"
