from app.agent.execution_policy_guard import (
    POLICY_ALLOW,
    POLICY_ALLOW_WITH_AUDIT,
    POLICY_BLOCK,
    POLICY_REQUIRE_APPROVAL,
    evaluate_tool_policy,
)
from app.agent.plan_execution_audit import build_plan_execution_audit


def make_plan(
    *,
    tools: list[str],
    targets: list[str],
    risk_level: str = "low",
) -> dict:
    return {
        "suggested_tools": tools,
        "target_paths": targets,
        "risk_level": risk_level,
        "needs_approval": risk_level == "high",
        "steps": [
            {"suggested_tool": tool_name}
            for tool_name in tools
        ],
    }


def test_planned_read_inside_scope_is_allowed():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["read_file"],
            targets=["demo_project/models.py"],
        ),
        tool_name="read_file",
        tool_args={"path": "demo_project/models.py"},
        risk_level="low",
    )

    assert decision["decision"] == POLICY_ALLOW
    assert decision["matched_paths"] == ["demo_project/models.py"]


def test_read_outside_scope_is_allowed_with_audit():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["read_file"],
            targets=["demo_project/models.py"],
        ),
        tool_name="read_file",
        tool_args={"path": "demo_project/service.py"},
        risk_level="low",
    )

    assert decision["decision"] == POLICY_ALLOW_WITH_AUDIT
    assert decision["outside_paths"] == ["demo_project/service.py"]


def test_unplanned_low_risk_tool_is_allowed_with_audit():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["read_file"],
            targets=["demo_project/models.py"],
        ),
        tool_name="search_code",
        tool_args={"path": "demo_project", "query": "User"},
        risk_level="low",
    )

    assert decision["decision"] == POLICY_ALLOW_WITH_AUDIT
    assert decision["planned"] is False


def test_planned_write_inside_scope_requires_approval():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["edit_file"],
            targets=["demo_project/models.py"],
            risk_level="high",
        ),
        tool_name="edit_file",
        tool_args={
            "path": r"demo_project\models.py",
            "old_text": "name",
            "new_text": "username",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_REQUIRE_APPROVAL
    assert decision["requires_approval"] is True
    assert decision["outside_paths"] == []


def test_unplanned_write_is_blocked_even_inside_scope():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["read_file"],
            targets=["demo_project/models.py"],
        ),
        tool_name="edit_file",
        tool_args={
            "path": "demo_project/models.py",
            "old_text": "name",
            "new_text": "username",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert any(
        item["code"] == "unplanned_risky_tool"
        for item in decision["violations"]
    )


def test_write_outside_scope_is_blocked():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["edit_file"],
            targets=["demo_project/models.py"],
            risk_level="high",
        ),
        tool_name="edit_file",
        tool_args={
            "path": "demo_project/service.py",
            "old_text": "name",
            "new_text": "username",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert decision["outside_paths"] == ["demo_project/service.py"]


def test_write_without_planned_target_is_blocked_fail_closed():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["write_new_file"],
            targets=[],
            risk_level="high",
        ),
        tool_name="write_new_file",
        tool_args={
            "path": "demo_project/config.py",
            "content": "DEBUG = False\n",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert any(
        item["code"] == "missing_plan_scope"
        for item in decision["violations"]
    )


def test_write_without_path_is_blocked_fail_closed():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["edit_file"],
            targets=["demo_project/models.py"],
            risk_level="high",
        ),
        tool_name="edit_file",
        tool_args={
            "old_text": "name",
            "new_text": "username",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert any(
        item["code"] == "missing_write_path"
        for item in decision["violations"]
    )


def test_planned_command_is_allowed():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["run_command"],
            targets=["demo_project/models.py"],
            risk_level="medium",
        ),
        tool_name="run_command",
        tool_args={
            "command": "python -m py_compile demo_project/models.py"
        },
        risk_level="medium",
    )

    assert decision["decision"] == POLICY_ALLOW


def test_planned_project_level_test_is_allowed_with_audit():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["run_command"],
            targets=["demo_project/models.py"],
            risk_level="medium",
        ),
        tool_name="run_command",
        tool_args={"command": "python -m pytest -q"},
        risk_level="medium",
    )

    assert decision["decision"] == POLICY_ALLOW_WITH_AUDIT


def test_unplanned_command_is_blocked():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["read_file"],
            targets=["demo_project/models.py"],
        ),
        tool_name="run_command",
        tool_args={"command": "python -m pytest -q"},
        risk_level="medium",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert any(
        item["code"] == "unplanned_command"
        for item in decision["violations"]
    )


def test_unclassified_high_risk_tool_is_blocked():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["dangerous_custom_tool"],
            targets=["demo_project/models.py"],
            risk_level="high",
        ),
        tool_name="dangerous_custom_tool",
        tool_args={"path": "demo_project/models.py"},
        risk_level="high",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert any(
        item["code"] == "unclassified_high_risk_tool"
        for item in decision["violations"]
    )


def test_audit_summarizes_policy_guard_decisions():
    plan = make_plan(
        tools=["edit_file"],
        targets=["demo_project/models.py"],
        risk_level="high",
    )
    policy_decision = evaluate_tool_policy(
        task_plan=plan,
        tool_name="edit_file",
        tool_args={
            "path": "demo_project/service.py",
            "old_text": "name",
            "new_text": "username",
        },
        risk_level="high",
    )

    audit = build_plan_execution_audit(
        task_plan=plan,
        execution_steps=[
            {
                "step": 1,
                "type": "policy_blocked",
                "tool_name": "edit_file",
                "tool_args": {
                    "path": "demo_project/service.py"
                },
                "risk_level": "high",
                "success": False,
                "policy_decision": policy_decision,
            }
        ],
    )

    assert audit["status"] == "requires_review"
    assert audit["policy_guard"]["checked_count"] == 1
    assert audit["policy_guard"]["blocked_count"] == 1
    assert audit["outcome"]["tool_call_count"] == 0
