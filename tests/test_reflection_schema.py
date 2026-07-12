from app.schemas import AgentStep


def test_agent_step_accepts_reflection():
    step = AgentStep(
        step=1,
        type="tool_call",
        tool_name="read_file",
        tool_args={
            "path": "demo_project/not_exists.py",
        },
        risk_level="low",
        tool_result="文件不存在：demo_project/not_exists.py",
        success=False,
        error={
            "type": "file_not_found",
            "message": "文件不存在",
            "detail": "demo_project/not_exists.py",
        },
        reflection={
            "trigger": "tool_error",
            "failed_tool": "read_file",
            "error_type": "file_not_found",
            "suggestion": "建议先 list_files。",
            "can_retry": True,
            "next_action_hint": "list_files",
        },
    )

    assert step.reflection is not None
    assert step.reflection["trigger"] == "tool_error"
    assert step.reflection["next_action_hint"] == "list_files"