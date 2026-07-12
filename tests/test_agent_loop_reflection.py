import json
from types import SimpleNamespace

import app.agent.agent_loop as agent_loop
from app.agent.agent_loop import run_agent_loop


def make_fake_tool_call(tool_name: str, tool_args: dict):
    """
    构造一个假的 tool_call 对象。

    为什么要这样写？
    因为真实 OpenAI/DeepSeek 返回的 tool_call 不是普通 dict，
    而是类似 object.function.name / object.function.arguments 这种属性访问结构。
    """
    return SimpleNamespace(
        id="call_test_001",
        function=SimpleNamespace(
            name=tool_name,
            arguments=json.dumps(tool_args, ensure_ascii=False),
        ),
    )


def make_fake_response(message):
    """
    构造一个假的模型响应对象。

    run_agent_loop 里会访问：
    response.choices[0].message

    所以这里也模拟同样的结构。
    """
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=message,
            )
        ]
    )


def test_agent_loop_adds_reflection_when_tool_fails(monkeypatch):
    """
    测试目标：

    当工具执行失败时，
    agent_loop 应该给当前 tool_call step 添加 reflection。
    """

    fake_tool_call = make_fake_tool_call(
        tool_name="read_file",
        tool_args={
            "path": "demo_project/not_exists.py",
        },
    )

    first_message = SimpleNamespace(
        content="我需要读取文件。",
        tool_calls=[fake_tool_call],
    )

    second_message = SimpleNamespace(
        content="文件读取失败，目标文件不存在。",
        tool_calls=None,
    )

    fake_responses = [
        make_fake_response(first_message),
        make_fake_response(second_message),
    ]

    def fake_create(*args, **kwargs):
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

    result = run_agent_loop(
        agent_name="TestAgent",
        system_prompt="你是一个测试 Agent。",
        tools=[],
        available_tools={
            "read_file": fake_read_file,
        },
        user_message="请读取 demo_project/not_exists.py",
        max_steps=3,
    )

    assert result["status"] == "finished"

    tool_step = result["steps"][0]

    assert tool_step["type"] == "tool_call"
    assert tool_step["tool_name"] == "read_file"
    assert tool_step["success"] is False
    assert tool_step["reflection"] is not None

    reflection = tool_step["reflection"]

    assert reflection["trigger"] == "tool_error"
    assert reflection["failed_tool"] == "read_file"
    assert reflection["error_type"] == "file_not_found"
    assert reflection["can_retry"] is True
    assert reflection["next_action_hint"] == "list_files"