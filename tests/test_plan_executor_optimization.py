from app.agent.plan_executor import (
    STEP_STATUS_COMPLETED_WITH_WARNINGS,
    create_executor_state,
    evaluate_executor_tool_call,
    mark_current_step_result,
    mark_final_answer_completed,
)


def _test_plan():
    return {
        "objective": "运行测试",
        "target_paths": ["demo_project/models.py"],
        "steps": [
            {
                "index": 1,
                "title": "运行验证命令",
                "description": "运行测试",
                "suggested_tool": "run_command",
                "risk_level": "medium",
                "reason": "测试",
            },
            {
                "index": 2,
                "title": "汇总",
                "description": "总结",
                "suggested_tool": None,
                "risk_level": "low",
                "reason": "总结",
            },
        ],
    }


def test_failed_pytest_then_py_compile_is_completed_with_warnings():
    state = create_executor_state(_test_plan())

    mark_current_step_result(
        state,
        tool_name="run_command",
        success=False,
        error={"type": "command_failed"},
        tool_args={"command": "python -m pytest -q", "cwd": "."},
    )
    mark_current_step_result(
        state,
        tool_name="run_command",
        success=True,
        error=None,
        tool_args={
            "command": "python -m py_compile demo_project/models.py",
            "cwd": ".",
        },
    )

    assert state["steps"][0]["status"] == STEP_STATUS_COMPLETED_WITH_WARNINGS
    assert state["steps"][0]["warnings"]
    assert state["current_step_position"] == 1

    mark_final_answer_completed(state)

    assert state["status"] == "completed"
    assert state["completion_status"] == "completed_with_warnings"
    assert state["warning_steps"] == 1


def test_validation_step_blocks_workspace_root_listing():
    state = create_executor_state(_test_plan())

    decision = evaluate_executor_tool_call(
        executor_state=state,
        tool_name="list_files",
        risk_level="low",
        tool_args={"path": "."},
    )

    assert decision["allowed"] is False
    assert decision["decision"] == "block_out_of_order"
    assert "workspace 根目录" in decision["reason"]
