from app.agent.plan_execution_audit import build_plan_execution_audit


def test_full_trace_treats_diff_tools_as_equivalent():
    task_plan = {
        "risk_level": "high",
        "needs_approval": True,
        "target_paths": ["demo_project/models.py"],
        "suggested_tools": [
            "read_file",
            "edit_file",
            "get_workspace_diff",
            "run_command",
        ],
        "steps": [
            {"suggested_tool": "read_file"},
            {"suggested_tool": "edit_file"},
            {"suggested_tool": "get_workspace_diff"},
            {"suggested_tool": "run_command"},
        ],
    }
    steps = [
        {
            "step": 1,
            "type": "tool_call",
            "tool_name": "read_file",
            "tool_args": {"path": "demo_project/models.py"},
            "success": True,
            "risk_level": "low",
        },
        {
            "step": 2,
            "type": "approval_required",
            "tool_name": "edit_file",
            "policy_decision": {"decision": "require_approval"},
        },
        {
            "step": 3,
            "type": "tool_call",
            "tool_name": "edit_file",
            "tool_args": {"path": "demo_project/models.py"},
            "success": True,
            "risk_level": "high",
            "policy_decision": {"decision": "allow"},
        },
        {
            "step": 4,
            "type": "tool_call",
            "tool_name": "get_file_diff",
            "tool_args": {"path": "demo_project/models.py"},
            "success": True,
            "risk_level": "low",
        },
        {
            "step": 5,
            "type": "tool_call",
            "tool_name": "run_command",
            "tool_args": {
                "command": "python -m pytest -q",
                "cwd": ".",
            },
            "success": True,
            "risk_level": "medium",
        },
    ]

    audit = build_plan_execution_audit(
        task_plan=task_plan,
        execution_steps=steps,
    )

    assert audit["unused_planned_tools"] == []
    assert audit["unplanned_tools"] == []
    assert audit["metrics"]["coverage"] == 1
    assert audit["metrics"]["precision"] == 1
    assert audit["status"] == "aligned"
    assert "get_workspace_diff" in audit["normalized_executed_tools"]
