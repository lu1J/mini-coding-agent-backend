import app.agent.verified_approval as approval
from app.agent.plan_executor import create_executor_state, mark_current_step_waiting_approval


def test_approval_resume_receives_preapproval_and_write_steps(monkeypatch):
    task_plan = {
        "objective": "修改并测试",
        "intents": ["edit", "test"],
        "target_paths": ["demo/main.py"],
        "suggested_tools": ["read_file", "edit_file", "run_command"],
        "steps": [
            {
                "index": 1,
                "title": "读取",
                "suggested_tool": "read_file",
                "risk_level": "low",
            },
            {
                "index": 2,
                "title": "修改",
                "suggested_tool": "edit_file",
                "risk_level": "high",
            },
            {
                "index": 3,
                "title": "测试",
                "suggested_tool": "run_command",
                "risk_level": "medium",
            },
        ],
    }
    state = create_executor_state(task_plan)
    state["steps"][0]["status"] = "completed"
    state["current_step_position"] = 1
    mark_current_step_waiting_approval(state, tool_name="edit_file")
    pre_steps = [
        {
            "step": 1,
            "type": "tool_call",
            "tool_name": "read_file",
            "tool_args": {"path": "demo/main.py"},
            "success": True,
        },
        {
            "step": 2,
            "type": "approval_required",
            "tool_name": "edit_file",
            "plan_step_index": 2,
            "plan_step_title": "修改",
            "executor_decision": {"decision": "allow_current_step"},
        },
    ]
    action = {
        "tool_name": "edit_file",
        "tool_args": {"path": "demo/main.py"},
        "risk_level": "high",
        "user_message": "修改并测试",
    }

    monkeypatch.setattr(approval, "read_pending_action", lambda _: action)
    monkeypatch.setattr(approval, "delete_pending_action", lambda _: None)
    monkeypatch.setattr(
        approval,
        "read_pending_execution_context",
        lambda _: {
            "task_plan": task_plan,
            "executor_state": state,
            "max_steps": 8,
            "steps": pre_steps,
        },
    )
    monkeypatch.setattr(
        approval,
        "delete_pending_execution_context",
        lambda _: None,
    )
    monkeypatch.setattr(
        approval,
        "evaluate_tool_policy",
        lambda **kwargs: {
            "decision": "require_approval",
            "allowed": True,
            "requires_approval": True,
            "reasons": [],
        },
    )
    monkeypatch.setattr(
        approval,
        "execute_verified_change",
        lambda **kwargs: {
            "success": True,
            "status": "passed",
            "changed_paths": ["demo/main.py"],
            "diff": {"total_added_lines": 1, "total_removed_lines": 0},
            "tool_execution": {
                "success": True,
                "result": "修改成功",
                "error": None,
            },
            "duration_ms": 5,
        },
    )

    captured = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        return {
            "status": "finished",
            "answer": "完成",
            "steps": kwargs["initial_steps"],
        }

    monkeypatch.setattr(
        approval,
        "_load_code_agent_runtime",
        lambda: (
            {"edit_file": lambda **kwargs: "ok"},
            fake_runner,
        ),
    )

    result = approval.execute_verified_approval(
        approval_id="approval_test",
        approved=True,
    )

    assert result["status"] == "approved"
    assert len(captured["initial_steps"]) == 3
    write_step = captured["initial_steps"][-1]
    assert write_step["type"] == "tool_call"
    assert write_step["tool_name"] == "edit_file"
    assert write_step["source"] == "approval_execution"
    assert write_step["policy_decision"]["approved_execution"] is True
