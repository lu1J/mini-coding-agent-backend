"""Day20.5：Planner 写意图识别可靠性专项测试。

背景（Day20 full eval 剩余 3 个确定性失败，均为 Planner 写意图缺口）：
- gv1_create_notes_approve：planner 只输出 analyze，无 write_new_file 步骤；
- gv1_modify_triple_pytest：planner 只输出 read/test，无 edit_file 步骤；
- gv1_diff_verify：planner 只输出 read/git，无 edit_file 步骤。

本测试锁定修复后的规则：
A. 10 条用户明确写意图正例 → 必须识别 edit；
B. 8 条影响分析/解释/建议负例 → 不得识别 edit；
C. 三个真实 golden 任务（用真实 eval case_root 前缀）→ 计划必须包含写步骤，
   且 read→edit→test 等步骤顺序正确；其余 13 个 golden 任务零行为回归；
D. golden 任务 Planner 输出的全部工具 ⊆ 真实 CODE_AGENT_TOOLS；
E. “解释/询问工具用途”语境守卫（怎么调用 edit_file、what does … 等）。
"""

import pytest

from app.agent.code_agent import CODE_AGENT_TOOLS
from app.agent.task_planner import (
    TASK_INTENT_EDIT,
    build_task_plan,
    detect_task_intents,
)
from evals.dataset import load_golden_tasks


REAL_TOOL_NAMES = {
    tool["function"]["name"]
    for tool in CODE_AGENT_TOOLS
    if tool.get("function", {}).get("name")
}

# ---------------------------------------------------------------------------
# A. 正例 10 条：用户明确表达真实写操作
# ---------------------------------------------------------------------------

WRITE_INTENT_POSITIVES = [
    # 1. 把 xxx.py 中 A 改成 B
    (
        "把 demo_project/math_utils.py 中 A 改成 B",
        "edit_file",
    ),
    # 2. 将 xxx.py 的 A 替换为 B
    (
        "将 demo_project/math_utils.py 的 A 替换为 B",
        "edit_file",
    ),
    # 3. 在 xxx.py 中添加三行注释
    (
        "在 demo_project/math_utils.py 中添加三行注释",
        "edit_file",
    ),
    # 4. 在 xxx.py 中新增一个函数
    (
        "在 demo_project/math_utils.py 中新增一个函数",
        "edit_file",
    ),
    # 5. 创建 notes.txt
    (
        "创建 demo_project/notes.txt",
        "write_new_file",
    ),
    # 6. 新建 notes.txt
    (
        "新建 demo_project/notes.txt",
        "write_new_file",
    ),
    # 7. 在某目录中写入一个新文件
    (
        "在 demo_project/data 目录中写入一个新文件",
        "edit_file",
    ),
    # 8. 请调用 write_new_file 创建 xxx
    (
        "请调用 write_new_file 创建 demo_project/notes.txt",
        "write_new_file",
    ),
    # 9. 请调用 edit_file 修改 xxx
    (
        "请调用 edit_file 修改 demo_project/config.py",
        "edit_file",
    ),
    # 10. 给 User 类新增 email 字段
    (
        "给 User 类新增 email 字段",
        "edit_file",
    ),
]


@pytest.mark.parametrize(
    "message,expected_write_tool",
    WRITE_INTENT_POSITIVES,
    ids=[f"positive_{i}" for i in range(1, 11)],
)
def test_positive_write_intent_is_detected(message, expected_write_tool):
    plan = build_task_plan(message)

    assert TASK_INTENT_EDIT in plan["intents"], (
        f"正例未被识别为写意图：{message}\nintents={plan['intents']}"
    )
    assert expected_write_tool in plan["suggested_tools"], (
        f"推荐工具缺少 {expected_write_tool}：{plan['suggested_tools']}"
    )
    assert plan["risk_level"] == "high"
    assert plan["needs_approval"] is True

    # 计划步骤必须真正出现对应写步骤。
    step_tools = [
        step["suggested_tool"]
        for step in plan["steps"]
    ]
    assert expected_write_tool in step_tools, (
        f"计划步骤缺少 {expected_write_tool} 写步骤：{step_tools}"
    )


# ---------------------------------------------------------------------------
# B. 负例 8 条：影响分析 / 建议 / 询问，不是写请求
# ---------------------------------------------------------------------------

WRITE_INTENT_NEGATIVES = [
    # 1. 分析修改 User 类的影响范围
    "分析修改 User 类的影响范围",
    # 2. 如果修改 User 会影响哪些文件
    "如果修改 User 会影响哪些文件",
    # 3. 告诉我应该如何修改
    "告诉我应该如何修改",
    # 4. 检查是否需要修改
    "检查是否需要修改",
    # 5. 分析新增 email 字段可能造成的影响
    "分析新增 email 字段可能造成的影响",
    # 6. 给我修改建议，但不要修改文件
    "给我修改建议，但不要修改文件",
    # 7. 只分析，不修改
    "只分析，不修改",
    # 8. 解释 edit_file 工具是做什么的
    "解释 edit_file 工具是做什么的",
]


@pytest.mark.parametrize(
    "message",
    WRITE_INTENT_NEGATIVES,
    ids=[f"negative_{i}" for i in range(1, 9)],
)
def test_negative_write_intent_is_not_detected(message):
    plan = build_task_plan(message)

    assert TASK_INTENT_EDIT not in plan["intents"], (
        f"负例被误判为写意图：{message}\nintents={plan['intents']}"
    )
    assert "edit_file" not in plan["suggested_tools"]
    assert "write_new_file" not in plan["suggested_tools"]
    assert plan["needs_approval"] is False


# ---------------------------------------------------------------------------
# C. 真实 golden 任务：Planner 离线回归
# ---------------------------------------------------------------------------

# Day20.5 修复的 3 个目标（此前 planner 完全不含写步骤）。
GOLDEN_TARGET_TASKS = {
    # id -> 必须出现的写工具（出现在建议工具与步骤中）
    "gv1_create_notes_approve": "write_new_file",
    "gv1_modify_triple_pytest": "edit_file",
    "gv1_diff_verify": "edit_file",
}

# Day20 基线本就含 edit 的任务（approve/reject 为已 PASS 的既有写任务，
# 不属于本轮 readonly 回归保护范围）。
GOLDEN_EDIT_TASKS_ALREADY = {
    "gv1_modify_docstring_approve",
    "gv1_modify_docstring_reject",
}

GOLDEN_CASE_ROOT_PREFIX = "workspace/eval_cases/run_1/{task_id}/demo_project"


def _golden_task_prompt(task_id: str) -> str:
    tasks = {t["id"]: t for t in load_golden_tasks()}
    assert task_id in tasks, f"数据集缺少任务 {task_id}"
    return tasks[task_id]["prompt_template"].format(
        case_root=GOLDEN_CASE_ROOT_PREFIX.format(task_id=task_id)
    )


def _golden_plan(task_id: str):
    return build_task_plan(_golden_task_prompt(task_id))


@pytest.mark.parametrize(
    "task_id,expected_write_tool",
    GOLDEN_TARGET_TASKS.items(),
)
def test_golden_write_tasks_now_include_write_step(task_id, expected_write_tool):
    """三个 Day20 确定性失败任务必须离线转正：
    planner 计划包含写意图与写步骤（不修改任何 golden task）。"""
    plan = _golden_plan(task_id)

    assert TASK_INTENT_EDIT in plan["intents"], (
        f"{task_id} 仍未识别写意图：{plan['intents']}"
    )

    assert expected_write_tool in plan["suggested_tools"], (
        f"{task_id} 建议工具缺少 {expected_write_tool}："
        f"{plan['suggested_tools']}"
    )

    step_tools = [
        step["suggested_tool"]
        for step in plan["steps"]
    ]
    assert expected_write_tool in step_tools, (
        f"{task_id} 步骤缺少 {expected_write_tool}：{step_tools}"
    )

    assert plan["risk_level"] == "high"
    assert plan["needs_approval"] is True


def test_golden_triple_pytest_step_order_is_read_edit_test():
    """gv1_modify_triple_pytest：步骤顺序至少能表达 read → edit → test。"""
    plan = _golden_plan("gv1_modify_triple_pytest")

    step_tools = [
        step["suggested_tool"]
        for step in plan["steps"]
    ]

    edit_index = step_tools.index("edit_file")
    test_index = step_tools.index("run_command")
    assert test_index > edit_index, (
        f"run_command 必须出现在 edit_file 之后：{step_tools}"
    )

    # 前面至少有一个读取步骤（read_file / read_file_lines）。
    assert any(
        tool in step_tools[:edit_index]
        for tool in ("read_file", "read_file_lines")
    ), f"edit 前缺少读取步骤：{step_tools}"


def test_golden_diff_verify_includes_edit_and_diff_steps():
    """gv1_diff_verify：计划必须同时包含 edit 与 diff 验证相关步骤。"""
    plan = _golden_plan("gv1_diff_verify")

    step_tools = [
        step["suggested_tool"]
        for step in plan["steps"]
    ]

    assert "edit_file" in step_tools
    diff_tools = {
        "get_workspace_diff",
        "get_git_diff",
        "get_file_diff",
    }
    assert diff_tools & set(step_tools), (
        f"缺少 diff 验证相关步骤：{step_tools}"
    )


def test_golden_readonly_tasks_are_unchanged_from_day20_baseline():
    """其余 11 个 golden 任务（Day20 基线中不含 edit 意图）零行为回归：
    Planner 修改不得让任何只读/分析/测试类任务意外获得写意图。"""
    tasks = {t["id"]: t for t in load_golden_tasks()}
    readonly_task_ids = sorted(
        set(tasks)
        - set(GOLDEN_TARGET_TASKS)
        - GOLDEN_EDIT_TASKS_ALREADY
    )

    assert len(readonly_task_ids) == 11

    for task_id in readonly_task_ids:
        plan = _golden_plan(task_id)

        assert TASK_INTENT_EDIT not in plan["intents"], (
            f"{task_id} 被新规则误判出写意图：{plan['intents']}"
        )
        assert "edit_file" not in plan["suggested_tools"], task_id
        assert "write_new_file" not in plan["suggested_tools"], task_id


# ---------------------------------------------------------------------------
# D. 全部 golden 任务的 Planner 工具 ⊆ 真实 CODE_AGENT_TOOLS
# ---------------------------------------------------------------------------


def test_golden_planner_tools_all_exist_in_real_catalog():
    tasks = {t["id"]: t for t in load_golden_tasks()}

    for task_id in sorted(tasks):
        plan = _golden_plan(task_id)

        for tool_name in plan["suggested_tools"]:
            assert tool_name in REAL_TOOL_NAMES, (
                f"{task_id} 推荐了不存在的工具：{tool_name}"
            )

        for step in plan["steps"]:
            suggested = step.get("suggested_tool")
            if suggested:
                assert suggested in REAL_TOOL_NAMES, (
                    f"{task_id} 步骤引用了不存在的工具：{suggested}"
                )


# ---------------------------------------------------------------------------
# E. “解释/询问工具用途”语境守卫
# ---------------------------------------------------------------------------

TOOL_EXPLANATION_QUESTIONS = [
    "怎么调用 edit_file",
    "如何使用 write_new_file 创建文件？",
    "解释 edit_file 工具是做什么的",
    "什么是 write_new_file，它有什么作用",
    "What does edit_file do in this project?",
    "Explain how write_new_file works",
]


@pytest.mark.parametrize(
    "message",
    TOOL_EXPLANATION_QUESTIONS,
    ids=[f"explain_{i}" for i in range(1, len(TOOL_EXPLANATION_QUESTIONS) + 1)],
)
def test_tool_explanation_context_is_not_write_intent(message):
    """工具名 + 解释/询问语境 ≠ 写意图（工具名必须与执行动词共同判断）。"""
    intents = detect_task_intents(message)

    assert TASK_INTENT_EDIT not in intents, (
        f"解释/询问语境被误判为写意图：{message}\nintents={intents}"
    )


def test_mixed_explanation_then_real_edit_command_is_still_edit():
    """先解释后真命令的混合句不能被解释语境误压：
    “解释一下，然后用 edit_file 修改 …” 是真写请求。"""
    plan = build_task_plan(
        "请先解释一下这个工具，然后用 edit_file 修改 "
        "demo_project/config.py"
    )

    assert TASK_INTENT_EDIT in plan["intents"]
    assert "edit_file" in plan["suggested_tools"]
