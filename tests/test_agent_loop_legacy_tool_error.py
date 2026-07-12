import json
from types import SimpleNamespace

from app.agent.agent_loop import execute_tool


def make_fake_tool_call(tool_name: str, tool_args: dict):
    return SimpleNamespace(
        id=f"call_{tool_name}",
        function=SimpleNamespace(
            name=tool_name,
            arguments=json.dumps(tool_args, ensure_ascii=False),
        ),
    )


def test_execute_tool_marks_legacy_file_not_found_string_as_failure():
    def fake_read_file(path: str):
        return f"文件不存在：{path}"

    result = execute_tool(
        tool_call=make_fake_tool_call(
            tool_name="read_file",
            tool_args={
                "path": "demo_project/not_exists.py",
            },
        ),
        available_tools={
            "read_file": fake_read_file,
        },
        agent_name="TestAgent",
    )

    assert result["tool_name"] == "read_file"
    assert result["tool_result"] == "文件不存在：demo_project/not_exists.py"
    assert result["success"] is False
    assert result["error"] is not None
    assert result["error"]["type"] == "file_not_found"


def test_execute_tool_keeps_normal_legacy_string_as_success():
    def fake_list_files(path: str = "."):
        return "[文件] demo_project/main.py"

    result = execute_tool(
        tool_call=make_fake_tool_call(
            tool_name="list_files",
            tool_args={
                "path": "demo_project",
            },
        ),
        available_tools={
            "list_files": fake_list_files,
        },
        agent_name="TestAgent",
    )

    assert result["tool_name"] == "list_files"
    assert result["success"] is True
    assert result["error"] is None