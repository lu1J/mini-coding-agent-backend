from app.agent.task_planner import (
    TASK_COMPLEXITY_COMPLEX,
    TASK_COMPLEXITY_MEDIUM,
    TASK_COMPLEXITY_SIMPLE,
    TASK_INTENT_ANALYZE,
    TASK_INTENT_EDIT,
    TASK_INTENT_READ,
    TASK_INTENT_SEARCH,
    TASK_INTENT_TEST,
    TASK_RISK_HIGH,
    TASK_RISK_LOW,
    TASK_RISK_MEDIUM,
    build_task_plan,
    detect_task_intents,
    extract_target_paths,
)


def test_detect_read_intent():
    intents = detect_task_intents("请读取 demo_project/main.py 并告诉我内容")

    assert TASK_INTENT_READ in intents


def test_detect_multiple_intents():
    intents = detect_task_intents("请修改 demo_project/main.py 并运行测试")

    assert TASK_INTENT_EDIT in intents
    assert TASK_INTENT_TEST in intents


def test_extract_target_paths():
    paths = extract_target_paths("请分析 `app/agent/agent_loop.py` 和 tests/test_agent_loop.py")

    assert "app/agent/agent_loop.py" in paths
    assert "tests/test_agent_loop.py" in paths


def test_build_read_plan():
    plan = build_task_plan("请读取 demo_project/main.py 并告诉我内容")

    assert plan["objective"] == "请读取 demo_project/main.py 并告诉我内容"
    assert TASK_INTENT_READ in plan["intents"]
    assert "demo_project/main.py" in plan["target_paths"]
    assert "read_file" in plan["suggested_tools"]
    assert plan["risk_level"] == TASK_RISK_LOW
    assert plan["complexity"] == TASK_COMPLEXITY_SIMPLE
    assert plan["needs_approval"] is False
    assert plan["estimated_steps"] >= 1


def test_build_search_analyze_plan():
    plan = build_task_plan("请分析 demo_project 的所有文件、接口、函数和潜在问题")

    assert TASK_INTENT_ANALYZE in plan["intents"]
    assert "list_files" in plan["suggested_tools"]
    assert "read_file" in plan["suggested_tools"]
    assert plan["complexity"] == TASK_COMPLEXITY_COMPLEX
    assert plan["risk_level"] == TASK_RISK_LOW
    assert plan["estimated_steps"] >= 2


def test_build_edit_plan_is_high_risk():
    plan = build_task_plan("请修改 demo_project/main.py，把返回内容改成 Hello")

    assert TASK_INTENT_EDIT in plan["intents"]
    assert "edit_file" in plan["suggested_tools"]
    assert plan["risk_level"] == TASK_RISK_HIGH
    assert plan["needs_approval"] is True

    step_tools = [
        step["suggested_tool"]
        for step in plan["steps"]
    ]

    assert "read_file" in step_tools
    assert "edit_file" in step_tools
    assert "get_workspace_diff" in step_tools


def test_build_test_plan_is_medium_risk():
    plan = build_task_plan("请运行 pytest 检查项目")

    assert TASK_INTENT_TEST in plan["intents"]
    assert "run_command" in plan["suggested_tools"]
    assert plan["risk_level"] == TASK_RISK_MEDIUM
    assert plan["needs_approval"] is False


def test_empty_task_defaults_to_analyze():
    plan = build_task_plan("")

    assert TASK_INTENT_ANALYZE in plan["intents"]
    assert plan["objective"] == ""
    assert plan["risk_level"] == TASK_RISK_LOW


def test_complex_edit_plan_adds_warning():
    plan = build_task_plan("请修改 demo_project/main.py，实现新接口")

    assert plan["complexity"] == TASK_COMPLEXITY_COMPLEX
    assert len(plan["warnings"]) >= 1
    assert any("高风险" in warning or "写文件" in warning for warning in plan["warnings"])


def test_search_plan_contains_search_code():
    plan = build_task_plan("请查找 hello 接口在哪里定义")

    assert TASK_INTENT_SEARCH in plan["intents"]
    assert "search_code" in plan["suggested_tools"]


def test_edit_plan_does_not_duplicate_read_steps():
    plan = build_task_plan("请修改 demo_project/main.py，把返回内容改成 Hello，并运行测试")

    read_steps = [
        step
        for step in plan["steps"]
        if step["suggested_tool"] == "read_file"
    ]

    assert len(read_steps) == 1
    assert read_steps[0]["title"] == "读取修改目标"