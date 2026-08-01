from app.agent.task_planner import (
    TASK_INTENT_ANALYZE,
    TASK_INTENT_EDIT,
    TASK_INTENT_READ,
    TASK_INTENT_SEARCH,
    TASK_INTENT_TEST,
    build_task_plan,
    detect_task_intents,
)


def test_impact_analysis_is_not_edit_or_test():
    """
    “修改后检查什么”只是影响分析，
    不应该被识别为 edit 或 test。
    """

    message = (
        "请分析 demo_project/models.py "
        "的影响范围，并读取直接依赖它的"
        "文件代码，告诉我修改 User 类后"
        "重点应该检查什么。"
    )

    plan = build_task_plan(message)

    assert (
        TASK_INTENT_READ
        in plan["intents"]
    )

    assert (
        TASK_INTENT_ANALYZE
        in plan["intents"]
    )

    assert (
        TASK_INTENT_EDIT
        not in plan["intents"]
    )

    assert (
        TASK_INTENT_TEST
        not in plan["intents"]
    )

    assert (
        "analyze_python_impact"
        in plan["suggested_tools"]
    )

    assert (
        "read_file"
        in plan["suggested_tools"]
    )

    assert (
        "edit_file"
        not in plan["suggested_tools"]
    )

    assert (
        "run_command"
        not in plan["suggested_tools"]
    )

    assert plan["risk_level"] == "low"
    assert (
        plan["needs_approval"]
        is False
    )


def test_explicit_edit_and_test_are_detected():
    """
    用户明确要求修改并运行测试时，
    应识别 edit 和 test。
    """

    message = (
        "请把 demo_project/models.py "
        "中的 name 改成 username，"
        "同步修改受影响文件并运行测试。"
    )

    plan = build_task_plan(message)

    assert (
        TASK_INTENT_EDIT
        in plan["intents"]
    )

    assert (
        TASK_INTENT_TEST
        in plan["intents"]
    )

    assert (
        "analyze_python_impact"
        in plan["suggested_tools"]
    )

    assert (
        "edit_file"
        in plan["suggested_tools"]
    )

    assert (
        "get_workspace_diff"
        in plan["suggested_tools"]
    )

    assert (
        "run_command"
        in plan["suggested_tools"]
    )

    assert plan["risk_level"] == "high"

    assert (
        plan["needs_approval"]
        is True
    )


def test_advisory_check_is_not_test():
    """
    “告诉我应该检查什么”
    不表示执行测试。
    """

    message = (
        "告诉我修改 User 类后"
        "应该检查哪些地方。"
    )

    intents = detect_task_intents(
        message
    )

    assert (
        TASK_INTENT_ANALYZE
        in intents
    )

    assert (
        TASK_INTENT_TEST
        not in intents
    )

    assert (
        TASK_INTENT_EDIT
        not in intents
    )


def test_explicit_pytest_request_is_test():
    """
    明确要求运行 pytest，
    应识别为 test。
    """

    message = (
        "请运行 pytest，"
        "验证修改是否正确。"
    )

    plan = build_task_plan(message)

    assert (
        TASK_INTENT_TEST
        in plan["intents"]
    )

    assert (
        "run_command"
        in plan["suggested_tools"]
    )

    assert (
        plan["risk_level"]
        == "medium"
    )


def test_python_symbol_query_uses_symbol_tool():
    """
    类或函数定义搜索应优先使用
    search_python_symbol。
    """

    message = (
        "请查找 UserService 类"
        "定义在哪里，并读取具体代码。"
    )

    plan = build_task_plan(message)

    assert (
        TASK_INTENT_SEARCH
        in plan["intents"]
    )

    assert (
        TASK_INTENT_READ
        in plan["intents"]
    )

    assert (
        "search_python_symbol"
        in plan["suggested_tools"]
    )

    assert (
        "read_file_lines"
        in plan["suggested_tools"]
    )

    assert (
        "search_code"
        not in plan["suggested_tools"]
    )


def test_python_outline_query_uses_outline_tool():
    """
    Python 文件结构分析应优先使用
    get_python_file_outline。
    """

    message = (
        "分析 demo_project/main.py "
        "中有哪些 import、类和函数。"
    )

    plan = build_task_plan(message)

    assert (
        "get_python_file_outline"
        in plan["suggested_tools"]
    )

    assert (
        "search_code"
        not in plan["suggested_tools"]
    )

    assert plan["risk_level"] == "low"


def test_dependency_query_uses_dependency_tool():
    """
    查询当前文件依赖谁时，
    应使用 get_python_dependencies。
    """

    message = (
        "请分析 demo_project/api.py "
        "依赖了哪些本地 Python 文件。"
    )

    plan = build_task_plan(message)

    assert (
        "get_python_dependencies"
        in plan["suggested_tools"]
    )

    assert (
        "analyze_python_impact"
        not in plan["suggested_tools"]
    )


def test_new_file_request_uses_write_new_file():
    """
    创建新文件应该推荐 write_new_file，
    而不是 edit_file。
    """

    message = (
        "请创建 "
        "demo_project/config.json 文件。"
    )

    plan = build_task_plan(message)

    assert (
        TASK_INTENT_EDIT
        in plan["intents"]
    )

    assert (
        "write_new_file"
        in plan["suggested_tools"]
    )

    assert (
        "edit_file"
        not in plan["suggested_tools"]
    )

    assert plan["risk_level"] == "high"


def test_impact_plan_steps_use_new_tool():
    """
    计划步骤中应该真正出现
    analyze_python_impact。
    """

    message = (
        "分析 demo_project/models.py "
        "可能影响哪些文件。"
    )

    plan = build_task_plan(message)

    step_tools = [
        step["suggested_tool"]
        for step in plan["steps"]
    ]

    assert (
        "analyze_python_impact"
        in step_tools
    )

    assert (
        "edit_file"
        not in step_tools
    )