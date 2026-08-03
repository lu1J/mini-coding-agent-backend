from app.agent.plan_execution_audit import (
    AUDIT_STATUS_ALIGNED,
    AUDIT_STATUS_PARTIAL,
    AUDIT_STATUS_REVIEW,
    build_plan_execution_audit,
)
from app.agent.plan_parameter_audit import normalize_audit_path


def make_tool_step(
    tool_name: str,
    tool_args: dict,
    *,
    risk_level: str = "low",
) -> dict:
    return {
        "type": "tool_call",
        "tool_name": tool_name,
        "tool_args": tool_args,
        "tool_result": "ok",
        "risk_level": risk_level,
        "success": True,
        "retry_from_reflection": False,
    }


def make_plan(
    *,
    tools: list[str],
    targets: list[str],
    risk_level: str = "low",
    needs_approval: bool = False,
) -> dict:
    return {
        "suggested_tools": tools,
        "target_paths": targets,
        "risk_level": risk_level,
        "needs_approval": needs_approval,
        "steps": [
            {"suggested_tool": tool_name}
            for tool_name in tools
        ],
    }


def test_windows_and_posix_paths_are_normalized():
    assert (
        normalize_audit_path(
            r".\\demo_project\\models.py"
        )
        == "demo_project/models.py"
    )

    assert (
        normalize_audit_path(
            "./demo_project/models.py"
        )
        == "demo_project/models.py"
    )


def test_exact_target_path_is_parameter_aligned():
    task_plan = make_plan(
        tools=[
            "analyze_python_impact",
            "read_file",
        ],
        targets=["demo_project/models.py"],
    )
    execution_steps = [
        make_tool_step(
            "analyze_python_impact",
            {
                "path": "demo_project/models.py",
                "project_root": "demo_project",
            },
        ),
        make_tool_step(
            "read_file",
            {"path": "demo_project/models.py"},
        ),
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    parameter = audit["parameter_audit"]

    assert audit["status"] == AUDIT_STATUS_ALIGNED
    assert parameter["status"] == "aligned"
    assert parameter["matched_target_paths"] == [
        "demo_project/models.py"
    ]
    assert parameter["write_paths_outside_targets"] == []


def test_dependency_reads_expand_scope_without_risk_escalation():
    task_plan = make_plan(
        tools=[
            "analyze_python_impact",
            "read_file",
        ],
        targets=["demo_project/models.py"],
    )
    execution_steps = [
        make_tool_step(
            "analyze_python_impact",
            {
                "path": "demo_project/models.py",
                "project_root": "demo_project",
            },
        ),
        make_tool_step(
            "read_file",
            {"path": "demo_project/service.py"},
        ),
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    parameter = audit["parameter_audit"]

    assert audit["status"] == AUDIT_STATUS_PARTIAL
    assert parameter["status"] == "expanded_read_scope"
    assert parameter["read_paths_outside_targets"] == [
        "demo_project/service.py"
    ]
    assert parameter["requires_review"] is False
    assert audit["risk"]["risk_escalated"] is False


def test_write_inside_planned_target_is_allowed():
    task_plan = make_plan(
        tools=["edit_file"],
        targets=["demo_project/models.py"],
        risk_level="high",
        needs_approval=True,
    )
    execution_steps = [
        make_tool_step(
            "edit_file",
            {
                "path": r"demo_project\\models.py",
                "old_text": "name",
                "new_text": "username",
            },
            risk_level="high",
        )
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    parameter = audit["parameter_audit"]

    assert audit["status"] == AUDIT_STATUS_ALIGNED
    assert parameter["write_scope_verified"] is True
    assert parameter["write_paths_outside_targets"] == []


def test_write_outside_planned_target_requires_review():
    task_plan = make_plan(
        tools=["edit_file"],
        targets=["demo_project/models.py"],
        risk_level="high",
        needs_approval=True,
    )
    execution_steps = [
        make_tool_step(
            "edit_file",
            {
                "path": "demo_project/service.py",
                "old_text": "name",
                "new_text": "username",
            },
            risk_level="high",
        )
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    parameter = audit["parameter_audit"]

    assert audit["status"] == AUDIT_STATUS_REVIEW
    assert parameter["requires_review"] is True
    assert parameter["write_paths_outside_targets"] == [
        "demo_project/service.py"
    ]


def test_directory_target_allows_child_file_write():
    task_plan = make_plan(
        tools=["write_new_file"],
        targets=["demo_project"],
        risk_level="high",
        needs_approval=True,
    )
    execution_steps = [
        make_tool_step(
            "write_new_file",
            {
                "path": "demo_project/config.json",
                "content": "{}",
            },
            risk_level="high",
        )
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    assert audit["status"] == AUDIT_STATUS_ALIGNED
    assert (
        audit["parameter_audit"]["write_scope_verified"]
        is True
    )


def test_unplanned_command_execution_requires_review():
    task_plan = make_plan(
        tools=["read_file"],
        targets=["demo_project/models.py"],
    )
    execution_steps = [
        make_tool_step(
            "read_file",
            {"path": "demo_project/models.py"},
        ),
        make_tool_step(
            "run_command",
            {
                "command": (
                    "python -m py_compile "
                    "demo_project/models.py"
                )
            },
            risk_level="medium",
        ),
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    parameter = audit["parameter_audit"]

    assert audit["status"] == AUDIT_STATUS_REVIEW
    assert parameter["unexpected_command_execution"] is True
    assert parameter["command_calls"][0]["planned"] is False


def test_planned_command_records_target_reference():
    task_plan = make_plan(
        tools=["run_command"],
        targets=["demo_project/models.py"],
        risk_level="medium",
    )
    execution_steps = [
        make_tool_step(
            "run_command",
            {
                "command": (
                    "python -m py_compile "
                    "demo_project/models.py"
                )
            },
            risk_level="medium",
        )
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    parameter = audit["parameter_audit"]

    assert audit["status"] == AUDIT_STATUS_ALIGNED
    assert parameter["unexpected_command_execution"] is False
    assert parameter["matched_target_paths"] == [
        "demo_project/models.py"
    ]
    assert parameter["command_calls"][0][
        "mentioned_target_paths"
    ] == ["demo_project/models.py"]


def test_write_without_path_is_unverifiable_and_requires_review():
    task_plan = make_plan(
        tools=["edit_file"],
        targets=["demo_project/models.py"],
        risk_level="high",
        needs_approval=True,
    )
    execution_steps = [
        make_tool_step(
            "edit_file",
            {
                "old_text": "name",
                "new_text": "username",
            },
            risk_level="high",
        )
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
    )

    parameter = audit["parameter_audit"]

    assert audit["status"] == AUDIT_STATUS_REVIEW
    assert parameter["unverifiable_write_tools"]
    assert parameter["write_scope_verified"] is False
