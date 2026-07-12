import json
import copy
from types import SimpleNamespace

import app.agent.agent_loop as agent_loop
from app.agent.agent_loop import run_agent_loop


def make_fake_tool_call(tool_name: str, tool_args: dict):
    return SimpleNamespace(
        id=f"call_{tool_name}",
        function=SimpleNamespace(
            name=tool_name,
            arguments=json.dumps(tool_args, ensure_ascii=False),
        ),
    )


def make_fake_response(message):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=message,
            )
        ]
    )


def test_agent_loop_uses_reflection_retry_hint(monkeypatch):
    """
    测试目标：

    当第一个工具失败时：
    - agent_loop 生成 reflection
    - retry_from_reflection=True
    - 下一轮模型能看到 [失败自省] 提示
    - 模型可以根据提示选择新的工具
    """

    first_message = SimpleNamespace(
        content="我先读取目标文件。",
        tool_calls=[
            make_fake_tool_call(
                tool_name="read_file",
                tool_args={
                    "path": "demo_project/not_exists.py",
                },
            )
        ],
    )

    second_message = SimpleNamespace(
        content="文件不存在，我先查看目录。",
        tool_calls=[
            make_fake_tool_call(
                tool_name="list_files",
                tool_args={},
            )
        ],
    )

    final_message = SimpleNamespace(
        content="目标文件不存在，我已经查看了目录结构。",
        tool_calls=None,
    )

    fake_responses = [
        make_fake_response(first_message),
        make_fake_response(second_message),
        make_fake_response(final_message),
    ]

    captured_model_messages = []

    def fake_create(*args, **kwargs):
        captured_model_messages.append(copy.deepcopy(kwargs["messages"]))
        return fake_responses.pop(0)

    monkeypatch.setattr(
        agent_loop.llm.client.chat.completions,
        "create",
        fake_create,
    )

    def fake_read_file(path: str):
        return {
            "success": False,
            "result": f"文件不存在：{path}",
            "error": {
                "type": "file_not_found",
                "message": "文件不存在",
                "detail": path,
            },
        }

    def fake_list_files():
        return {
            "success": True,
            "result": "demo_project/main.py\ndemo_project/README.md",
            "error": None,
        }

    result = run_agent_loop(
        agent_name="TestAgent",
        system_prompt="你是一个测试 Agent。",
        tools=[],
        available_tools={
            "read_file": fake_read_file,
            "list_files": fake_list_files,
        },
        user_message="请读取 demo_project/not_exists.py",
        max_steps=5,
    )

    assert result["status"] == "finished"

    first_tool_step = result["steps"][0]

    assert first_tool_step["type"] == "tool_call"
    assert first_tool_step["tool_name"] == "read_file"
    assert first_tool_step["success"] is False
    assert first_tool_step["reflection"] is not None
    assert first_tool_step["retry_from_reflection"] is True
    assert first_tool_step["reflection"]["next_action_hint"] == "list_files"

    second_tool_step = result["steps"][1]

    assert second_tool_step["type"] == "tool_call"
    assert second_tool_step["tool_name"] == "list_files"
    assert second_tool_step["success"] is True

    second_model_call_messages = captured_model_messages[1]
    last_message_before_retry = second_model_call_messages[-1]

    assert last_message_before_retry["role"] == "tool"
    assert "[失败自省]" in last_message_before_retry["content"]
    assert "建议下一步工具：list_files" in last_message_before_retry["content"]