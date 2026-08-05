from app.agent import verified_approval
from app.agent.plan_executor import (
    create_executor_state,
    mark_current_step_result,
    mark_current_step_waiting_approval,
)


def _build_plan():
    return {
        "objective": "修改并测试 demo_project/models.py",
        "intents": ["edit", "test"],
        "target_paths": ["demo_project/models.py"],
        "steps": [
            {
                "index": 1,
                "title": "读取修改目标",
                "description": "读取文件",
                "suggested_tool": "read_file",
                "risk_level": "low",
                "reason": "避免盲目修改",
            },
            {
                "index": 2,
                "title": "执行代码修改",
                "description": "修改文件",
                "suggested_tool": "edit_file",
                "risk_level": "high",
                "reason": "用户明确要求",
            },
            {
                "index": 3,
                "title": "运行验证命令",
                "description": "运行测试",
                "suggested_tool": "run_command",
                "risk_level": "medium",
                "reason": "验证修改",
            },
            {
                "index": 4,
                "title": "汇总结果",
                "description": "生成最终回答",
                "suggested_tool": None,
                "risk_level": "low",
                "reason": "向用户汇报",
            },
        ],
    }


def test_verified_approval_resumes_from_next_plan_step(monkeypatch):
    task_plan = _build_plan()
    executor_state = create_executor_state(task_plan)
    mark_current_step_result(
        executor_state,
        tool_name="read_file",
        success=True,
    )
    mark_current_step_waiting_approval(
        executor_state,
        tool_name="edit_file",
    )

    monkeypatch.setattr(
        verified_approval,
        "read_pending_action",
        lambda approval_id: {
            "tool_name": "edit_file",
            "tool_args": {"path": "demo_project/models.py"},
            "risk_level": "high",
            "user_message": "修改并测试 demo_project/models.py",
        },
    )
    monkeypatch.setattr(
        verified_approval,
        "read_pending_execution_context",
        lambda approval_id: {
            "task_plan": task_plan,
            "executor_state": executor_state,
            "max_steps": 8,
        },
    )
    monkeypatch.setattr(
        verified_approval,
        "delete_pending_action",
        lambda approval_id: None,
    )
    monkeypatch.setattr(
        verified_approval,
        "delete_pending_execution_context",
        lambda approval_id: None,
    )
    monkeypatch.setattr(
        verified_approval,
        "evaluate_tool_policy",
        lambda **kwargs: {"decision": "require_approval"},
    )
    monkeypatch.setattr(
        verified_approval,
        "execute_verified_change",
        lambda **kwargs: {
            "success": True,
            "status": "passed",
            "changed_paths": ["demo_project/models.py"],
            "diff": {
                "total_added_lines": 1,
                "total_removed_lines": 0,
            },
            "tool_execution": {"result": "ok"},
        },
    )

    captured: dict = {}

    def fake_run_agent_loop(**kwargs):
        captured.update(kwargs)
        return {
            "status": "finished",
            "executor_state": kwargs["executor_state_override"],
        }

    monkeypatch.setattr(
        verified_approval,
        "_load_code_agent_runtime",
        lambda: {
            "available_tools": {
                "edit_file": lambda **kwargs: "ok",
            },
            "system_prompt": "system",
            "tools": [],
            "run_agent_loop": fake_run_agent_loop,
        },
    )

    result = verified_approval.execute_verified_approval(
        approval_id="approval_test",
        approved=True,
    )

    assert result["status"] == "approved"
    assert captured["task_plan_override"] is task_plan
    resumed_state = captured["executor_state_override"]
    assert resumed_state["current_step_position"] == 2
    assert resumed_state["steps"][2]["suggested_tool"] == "run_command"
    assert "工具参数" not in captured["resume_context"]
