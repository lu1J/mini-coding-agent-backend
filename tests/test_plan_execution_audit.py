from app.agent.plan_execution_audit import (
    AUDIT_STATUS_ALIGNED,
    AUDIT_STATUS_NOT_EXECUTED,
    AUDIT_STATUS_PARTIAL,
    AUDIT_STATUS_REVIEW,
    attach_plan_execution_audit,
    build_plan_execution_audit,
)


def make_tool_step(
    tool_name: str,
    *,
    risk_level: str = "low",
    success: bool = True,
    retry: bool = False,
) -> dict:
    return {
        "type": "tool_call",
        "tool_name": tool_name,
        "tool_args": {},
        "tool_result": "ok",
        "risk_level": risk_level,
        "success": success,
        "retry_from_reflection": retry,
    }


def make_final_step() -> dict:
    return {
        "type": "final_answer",
        "content": "完成",
        "success": True,
    }


def test_exact_plan_execution_alignment():
    task_plan = {
        "suggested_tools": [
            "search_python_symbol",
            "read_file_lines",
        ],
        "risk_level": "low",
        "needs_approval": False,
        "steps": [
            {"suggested_tool": "search_python_symbol"},
            {"suggested_tool": "read_file_lines"},
        ],
    }
    execution_steps = [
        make_tool_step("search_python_symbol"),
        make_tool_step("read_file_lines"),
        make_final_step(),
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    assert audit["status"] == AUDIT_STATUS_ALIGNED
    assert audit["metrics"]["coverage"] == 1.0
    assert audit["metrics"]["precision"] == 1.0
    assert audit["metrics"]["order_score"] == 1.0
    assert audit["metrics"]["alignment_score"] == 1.0
    assert not audit["unplanned_tools"]
    assert not audit["unused_planned_tools"]


def test_low_risk_extra_tool_is_partial_alignment():
    task_plan = {
        "suggested_tools": [
            "analyze_python_impact",
            "read_file",
        ],
        "risk_level": "low",
        "needs_approval": False,
        "steps": [
            {"suggested_tool": "analyze_python_impact"},
            {"suggested_tool": "read_file"},
        ],
    }
    execution_steps = [
        make_tool_step("list_files"),
        make_tool_step("read_file"),
        make_tool_step("analyze_python_impact"),
        make_tool_step("read_file"),
        make_tool_step("read_file"),
        make_final_step(),
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    assert audit["status"] == AUDIT_STATUS_PARTIAL
    assert "list_files" in audit["unplanned_tools"]
    assert audit["risk"]["risk_escalated"] is False
    assert audit["risk"]["actual_max_risk"] == "low"


def test_unplanned_high_risk_tool_requires_review():
    task_plan = {
        "suggested_tools": ["read_file"],
        "risk_level": "low",
        "needs_approval": False,
        "steps": [
            {"suggested_tool": "read_file"},
        ],
    }
    execution_steps = [
        make_tool_step("read_file"),
        make_tool_step("edit_file", risk_level="high"),
        make_final_step(),
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    assert audit["status"] == AUDIT_STATUS_REVIEW
    assert "edit_file" in audit["unplanned_risky_tools"]
    assert audit["risk"]["risk_escalated"] is True
    assert audit["risk"]["approval_expectation_mismatch"] is True


def test_failed_and_retry_calls_are_counted():
    task_plan = {
        "suggested_tools": ["search_code"],
        "risk_level": "low",
        "needs_approval": False,
    }
    execution_steps = [
        make_tool_step("search_code", success=False),
        make_tool_step("search_code", success=True, retry=True),
        make_final_step(),
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )
    outcome = audit["outcome"]

    assert outcome["tool_call_count"] == 2
    assert outcome["successful_tool_calls"] == 1
    assert outcome["failed_tool_calls"] == 1
    assert outcome["retry_tool_calls"] == 1
    assert outcome["executed_tool_counts"]["search_code"] == 2


def test_no_tool_execution_status():
    task_plan = {
        "suggested_tools": ["read_file"],
        "risk_level": "low",
        "needs_approval": False,
    }

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=[make_final_step()],
    )

    assert audit["status"] == AUDIT_STATUS_NOT_EXECUTED
    assert audit["outcome"]["tool_call_count"] == 0


def test_waiting_approval_is_not_counted_as_execution():
    task_plan = {
        "suggested_tools": ["edit_file"],
        "risk_level": "high",
        "needs_approval": True,
    }
    execution_steps = [
        {
            "type": "approval_required",
            "tool_name": "edit_file",
            "risk_level": "high",
            "success": None,
        }
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    assert audit["status"] == AUDIT_STATUS_NOT_EXECUTED
    assert audit["outcome"]["approval_required_count"] == 1
    assert audit["risk"]["actual_high_risk_tools"] == []


def test_attach_audit_keeps_original_result():
    result = {
        "status": "finished",
        "answer": "完成",
        "task_plan": {
            "suggested_tools": ["read_file"],
            "risk_level": "low",
            "needs_approval": False,
        },
        "steps": [
            make_tool_step("read_file"),
            make_final_step(),
        ],
    }

    attached = attach_plan_execution_audit(result)

    assert attached["status"] == "finished"
    assert attached["answer"] == "完成"
    assert "plan_execution_audit" in attached
    assert (
        attached["plan_execution_audit"]["status"]
        == AUDIT_STATUS_ALIGNED
    )
