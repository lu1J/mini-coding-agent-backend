from app.agent.plan_executor import (
    EXECUTOR_ALLOW_CURRENT,
    EXECUTOR_ALLOW_SUPPORTING,
    EXECUTOR_BLOCK_OUT_OF_ORDER,
    EXECUTOR_STATUS_COMPLETED,
    EXECUTOR_STATUS_READY_FOR_FINAL,
    can_accept_final_answer,
    create_executor_state,
    evaluate_executor_tool_call,
    get_current_executor_step,
    mark_approved_step_completed,
    mark_current_step_result,
    mark_current_step_waiting_approval,
    mark_final_answer_completed,
    mark_supporting_tool_used,
)


def build_plan():
    return {
        "objective": "修改并测试 models.py",
        "target_paths": ["demo_project/models.py"],
        "steps": [
            {
                "index": 1,
                "title": "读取修改目标",
                "description": "先读取文件",
                "suggested_tool": "read_file",
                "risk_level": "low",
                "reason": "避免盲目修改",
            },
            {
                "index": 2,
                "title": "执行修改",
                "description": "修改文件",
                "suggested_tool": "edit_file",
                "risk_level": "high",
                "reason": "用户明确要求",
            },
            {
                "index": 3,
                "title": "运行测试",
                "description": "运行 pytest",
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


def test_create_executor_state_points_to_first_step():
    state = create_executor_state(build_plan())
    current = get_current_executor_step(state)
    assert current["plan_step_index"] == 1
    assert current["suggested_tool"] == "read_file"
    assert state["completed_steps"] == 0


def test_current_step_tool_is_allowed():
    state = create_executor_state(build_plan())
    decision = evaluate_executor_tool_call(
        executor_state=state,
        tool_name="read_file",
        risk_level="low",
    )
    assert decision["decision"] == EXECUTOR_ALLOW_CURRENT
    assert decision["completes_current_step"] is True


def test_equivalent_read_tool_is_allowed():
    state = create_executor_state(build_plan())
    decision = evaluate_executor_tool_call(
        executor_state=state,
        tool_name="read_file_lines",
        risk_level="low",
    )
    assert decision["decision"] == EXECUTOR_ALLOW_CURRENT


def test_one_supporting_low_risk_tool_is_allowed():
    state = create_executor_state(build_plan())
    first = evaluate_executor_tool_call(
        executor_state=state,
        tool_name="list_files",
        risk_level="low",
    )
    assert first["decision"] == EXECUTOR_ALLOW_SUPPORTING
    mark_supporting_tool_used(state, tool_name="list_files")

    second = evaluate_executor_tool_call(
        executor_state=state,
        tool_name="search_code",
        risk_level="low",
    )
    assert second["decision"] == EXECUTOR_BLOCK_OUT_OF_ORDER


def test_out_of_order_high_risk_tool_is_blocked():
    state = create_executor_state(build_plan())
    decision = evaluate_executor_tool_call(
        executor_state=state,
        tool_name="edit_file",
        risk_level="high",
    )
    assert decision["decision"] == EXECUTOR_BLOCK_OUT_OF_ORDER
    assert decision["allowed"] is False


def test_success_advances_to_next_plan_step():
    state = create_executor_state(build_plan())
    mark_current_step_result(
        state,
        tool_name="read_file",
        success=True,
    )
    current = get_current_executor_step(state)
    assert current["plan_step_index"] == 2
    assert state["completed_steps"] == 1


def test_failure_keeps_same_step_for_retry():
    state = create_executor_state(build_plan())
    mark_current_step_result(
        state,
        tool_name="read_file",
        success=False,
        error={"type": "file_not_found"},
    )
    current = get_current_executor_step(state)
    assert current["plan_step_index"] == 1
    assert current["status"] == "retrying"


def test_waiting_approval_does_not_advance():
    state = create_executor_state(build_plan())
    mark_current_step_result(state, tool_name="read_file", success=True)
    mark_current_step_waiting_approval(state, tool_name="edit_file")
    assert state["status"] == "waiting_approval"
    assert get_current_executor_step(state)["plan_step_index"] == 2


def test_approved_write_advances_to_test_step():
    state = create_executor_state(build_plan())
    mark_current_step_result(state, tool_name="read_file", success=True)
    mark_current_step_waiting_approval(state, tool_name="edit_file")
    mark_approved_step_completed(state, tool_name="edit_file")
    current = get_current_executor_step(state)
    assert current["plan_step_index"] == 3
    assert current["suggested_tool"] == "run_command"


def test_final_answer_only_allowed_at_summary_step():
    state = create_executor_state(build_plan())
    assert can_accept_final_answer(state) is False

    mark_current_step_result(state, tool_name="read_file", success=True)
    mark_approved_step_completed(state, tool_name="edit_file")
    mark_current_step_result(state, tool_name="run_command", success=True)

    assert state["status"] == EXECUTOR_STATUS_READY_FOR_FINAL
    assert can_accept_final_answer(state) is True

    mark_final_answer_completed(state)
    assert state["status"] == EXECUTOR_STATUS_COMPLETED
