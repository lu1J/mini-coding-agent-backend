from app.agent.reflection import (
    build_approval_rejected_reflection,
    build_max_steps_reflection,
    build_model_error_reflection,
    build_tool_error_reflection,
    extract_error_message,
    extract_error_type,
    should_retry_from_reflection,
)


def test_extract_error_type_from_error_dict():
    tool_result = {
        "error": {
            "type": "command_failed",
            "message": "命令执行失败",
        }
    }

    assert extract_error_type(tool_result=tool_result) == "command_failed"


def test_extract_error_type_from_error_text_file_not_found():
    assert extract_error_type(error="file not found") == "file_not_found"


def test_extract_error_message_from_error_dict():
    tool_result = {
        "error": {
            "type": "command_failed",
            "message": "命令执行失败",
            "detail": "SyntaxError: invalid syntax",
        }
    }

    message = extract_error_message(tool_result=tool_result)

    assert "命令执行失败" in message
    assert "SyntaxError" in message


def test_build_tool_error_reflection_for_file_not_found():
    reflection = build_tool_error_reflection(
        tool_name="read_file",
        tool_args={
            "path": "demo_project/not_exists.py",
        },
        error="file not found",
    )

    assert reflection["trigger"] == "tool_error"
    assert reflection["failed_tool"] == "read_file"
    assert reflection["error_type"] == "file_not_found"
    assert reflection["can_retry"] is True
    assert reflection["next_action_hint"] == "list_files"
    assert "路径不存在" in reflection["suggestion"] or "不存在" in reflection["suggestion"]


def test_build_tool_error_reflection_for_command_failed():
    tool_result = {
        "error": {
            "type": "command_failed",
            "message": "命令执行失败",
            "detail": "SyntaxError",
        }
    }

    reflection = build_tool_error_reflection(
        tool_name="run_command",
        tool_args={
            "command": "python -m py_compile demo_project/main.py",
        },
        tool_result=tool_result,
    )

    assert reflection["error_type"] == "command_failed"
    assert reflection["can_retry"] is True
    assert reflection["next_action_hint"] == "read_file_lines"
    assert "命令执行失败" in reflection["suggestion"]


def test_build_max_steps_reflection():
    reflection = build_max_steps_reflection(
        max_steps=5,
        completed_steps=5,
        user_message="分析 demo_project 并修复问题",
    )

    assert reflection["trigger"] == "max_steps_reached"
    assert reflection["error_type"] == "max_steps_reached"
    assert reflection["can_retry"] is True
    assert "增加 max_steps" in reflection["suggestion"]


def test_build_approval_rejected_reflection():
    reflection = build_approval_rejected_reflection(
        tool_name="write_new_file",
        tool_args={
            "path": "demo_project/new_file.py",
        },
    )

    assert reflection["trigger"] == "approval_rejected"
    assert reflection["failed_tool"] == "write_new_file"
    assert reflection["can_retry"] is False
    assert "拒绝" in reflection["error_message"]


def test_build_model_error_reflection():
    reflection = build_model_error_reflection(
        error="APIConnectionError",
    )

    assert reflection["trigger"] == "model_error"
    assert reflection["error_type"] == "model_error"
    assert reflection["can_retry"] is True
    assert "APIConnectionError" in reflection["error_message"]


def test_should_retry_from_reflection():
    reflection = {
        "can_retry": True,
    }

    assert should_retry_from_reflection(
        reflection=reflection,
        retry_count=0,
        max_retries=1,
    ) is True

    assert should_retry_from_reflection(
        reflection=reflection,
        retry_count=1,
        max_retries=1,
    ) is False


def test_should_not_retry_when_reflection_disallows_retry():
    reflection = {
        "can_retry": False,
    }

    assert should_retry_from_reflection(
        reflection=reflection,
        retry_count=0,
        max_retries=1,
    ) is False