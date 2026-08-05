from app.agent.task_planner import (
    TASK_INTENT_ANALYZE,
    TASK_INTENT_EDIT,
    TASK_INTENT_TEST,
    build_task_plan,
    detect_task_intents,
)


def test_location_based_add_comment_is_explicit_edit_and_test():
    message = (
        "请在 demo_project/models.py 的 User 类上方"
        "添加注释“# 用户模型”，并在修改后运行测试。"
    )

    plan = build_task_plan(message)

    assert TASK_INTENT_EDIT in plan["intents"]
    assert TASK_INTENT_TEST in plan["intents"]
    assert "edit_file" in plan["suggested_tools"]
    assert "run_command" in plan["suggested_tools"]
    assert plan["risk_level"] == "high"
    assert plan["needs_approval"] is True


def test_location_based_insert_is_explicit_edit():
    intents = detect_task_intents(
        "在 app/main.py 的 hello 函数下方插入一行日志。"
    )

    assert TASK_INTENT_EDIT in intents


def test_give_class_add_field_is_explicit_edit():
    plan = build_task_plan(
        "帮我给 User 类增加 email 字段。"
    )

    assert TASK_INTENT_EDIT in plan["intents"]
    assert "edit_file" in plan["suggested_tools"]


def test_append_readme_content_is_explicit_edit():
    plan = build_task_plan(
        "请向 README.md 追加一段安装说明。"
    )

    assert TASK_INTENT_EDIT in plan["intents"]
    assert "edit_file" in plan["suggested_tools"]


def test_analyze_add_field_impact_is_not_edit():
    intents = detect_task_intents(
        "分析给 User 类增加 email 字段后会影响哪些文件。"
    )

    assert TASK_INTENT_ANALYZE in intents
    assert TASK_INTENT_EDIT not in intents


def test_advisory_after_adding_comment_is_not_edit_or_test():
    plan = build_task_plan(
        "告诉我在 User 类上方添加注释后应该检查什么。"
    )

    assert TASK_INTENT_ANALYZE in plan["intents"]
    assert TASK_INTENT_EDIT not in plan["intents"]
    assert TASK_INTENT_TEST not in plan["intents"]
    assert "edit_file" not in plan["suggested_tools"]
    assert "run_command" not in plan["suggested_tools"]
