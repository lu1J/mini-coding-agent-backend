import json
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


def test_agent_loop_adds_reflection_when_max_steps_reached(monkeypatch):
    """
    测试目标：

    当 Agent 达到 max_steps 时，
    应该追加一个 reflection step，
    说明任务因为最大步数限制而停止。
    """

    first_message = SimpleNamespace(
        content="我先查看文件列表。",
        tool_calls=[
            make_fake_tool_call(
                tool_name="list_files",
                tool_args={},
            )
        ],
    )

    fake_responses = [
        make_fake_response(first_message),
    ]

    def fake_create(*args, **kwargs):
        return fake_responses.pop(0)

    monkeypatch.setattr(
        agent_loop.llm.client.chat.completions,
        "create",
        fake_create,
    )

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
            "list_files": fake_list_files,
        },
        user_message="请分析整个 demo_project。",
        max_steps=1,
    )

    assert result["status"] == "max_steps_reached"

    assert len(result["steps"]) == 2

    tool_step = result["steps"][0]

    assert tool_step["type"] == "tool_call"
    assert tool_step["tool_name"] == "list_files"
    assert tool_step["success"] is True

    reflection_step = result["steps"][1]

    assert reflection_step["type"] == "reflection"
    assert reflection_step["success"] is False
    assert reflection_step["reflection"] is not None

    reflection = reflection_step["reflection"]

    assert reflection["trigger"] == "max_steps_reached"
    assert reflection["error_type"] == "max_steps_reached"
    assert reflection["can_retry"] is True
    assert "增加 max_steps" in reflection["suggestion"]