from app.agent.task_planner import (
    PLANNER_VERSION,
    TASK_INTENT_EDIT,
    TASK_INTENT_TEST,
    build_task_plan,
)


def get_suppressed_intent_names(
    plan: dict,
) -> set[str]:
    """
    从 planner_meta 中取出被抑制的意图名称。
    """

    return {
        item["intent"]
        for item
        in plan["planner_meta"][
            "suppressed_intents"
        ]
    }


def test_impact_analysis_explains_suppressed_intents():
    """
    影响分析任务中出现“修改”和“检查”，
    Planner 应记录为什么没有加入
    edit 和 test。
    """

    message = (
        "请分析 demo_project/models.py "
        "的影响范围，并读取直接依赖它的"
        "文件代码，告诉我修改 User 类后"
        "重点应该检查什么。"
    )

    plan = build_task_plan(message)

    meta = plan["planner_meta"]

    assert (
        meta["version"]
        == PLANNER_VERSION
    )

    suppressed_names = (
        get_suppressed_intent_names(
            plan
        )
    )

    assert (
        TASK_INTENT_EDIT
        in suppressed_names
    )

    assert (
        TASK_INTENT_TEST
        in suppressed_names
    )

    assert (
        "python_impact"
        in meta["context_flags"]
    )

    assert (
        meta["confidence"]["level"]
        == "high"
    )

    assert (
        meta["confidence"]["score"]
        >= 0.80
    )


def test_explicit_edit_has_positive_evidence():
    """
    真正的编辑任务应该存在
    explicit_pattern 证据。
    """

    message = (
        "请把 demo_project/models.py "
        "中的 name 改成 username。"
    )

    plan = build_task_plan(message)

    meta = plan["planner_meta"]

    edit_evidence = (
        meta["intent_evidence"][
            TASK_INTENT_EDIT
        ]
    )

    evidence_types = {
        item["type"]
        for item in edit_evidence
    }

    assert (
        "explicit_pattern"
        in evidence_types
    )

    suppressed_names = (
        get_suppressed_intent_names(
            plan
        )
    )

    assert (
        TASK_INTENT_EDIT
        not in suppressed_names
    )

    assert plan["needs_approval"] is True


def test_advisory_check_explains_test_suppression():
    """
    “告诉我检查什么”不运行测试，
    并且 Planner 应给出抑制原因。
    """

    message = (
        "告诉我修改 User 类后"
        "应该检查哪些地方。"
    )

    plan = build_task_plan(message)

    suppressed = (
        plan["planner_meta"][
            "suppressed_intents"
        ]
    )

    test_suppression = next(
        item
        for item in suppressed
        if item["intent"]
        == TASK_INTENT_TEST
    )

    assert "检查" in (
        test_suppression[
            "matched_keywords"
        ]
    )

    assert (
        "不表示要求运行测试"
        in test_suppression["reason"]
    )

    assert (
        "run_command"
        not in plan["suggested_tools"]
    )


def test_symbol_search_has_context_evidence():
    """
    Python 符号搜索应该记录
    python_symbol_search 上下文。
    """

    message = (
        "请查找 UserService 类"
        "定义在哪里，并读取具体代码。"
    )

    plan = build_task_plan(message)

    meta = plan["planner_meta"]

    assert (
        "python_symbol_search"
        in meta["context_flags"]
    )

    search_evidence = (
        meta["intent_evidence"][
            "search"
        ]
    )

    context_values = {
        item["value"]
        for item in search_evidence
        if item["type"] == "context"
    }

    assert (
        "python_symbol_search"
        in context_values
    )


def test_planner_meta_keeps_old_plan_fields():
    """
    新增 planner_meta 后，
    原有 task_plan 字段必须继续存在。
    """

    plan = build_task_plan(
        "请读取 demo_project/main.py。"
    )

    expected_fields = {
        "objective",
        "intents",
        "target_paths",
        "suggested_tools",
        "risk_level",
        "complexity",
        "needs_approval",
        "estimated_steps",
        "steps",
        "warnings",
        "planner_meta",
    }

    assert expected_fields.issubset(
        plan.keys()
    )

    assert (
        plan["planner_meta"][
            "decision_summary"
        ]
    )