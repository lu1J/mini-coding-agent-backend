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


def test_agent_loop_step_numbers_are_unique_for_multiple_tool_calls(monkeypatch):
    first_message = SimpleNamespace(
        content="我需要先查看目录并读取文件。",
        tool_calls=[
            make_fake_tool_call(
                tool_name="list_files",
                tool_args={
                    "path": "demo_project",
                },
            ),
            make_fake_tool_call(
                tool_name="read_file",
                tool_args={
                    "path": "demo_project/main.py",
                },
            ),
        ],
    )

    final_message = SimpleNamespace(
        content="分析完成。",
        tool_calls=None,
    )

    fake_responses = [
        make_fake_response(first_message),
        make_fake_response(final_message),
    ]

    def fake_create(*args, **kwargs):
        return fake_responses.pop(0)

    monkeypatch.setattr(
        agent_loop.llm.client.chat.completions,
        "create",
        fake_create,
    )

    def fake_list_files(path: str = "."):
        return "[文件] demo_project/main.py"

    def fake_read_file(path: str):
        return "print('hello')"

    result = run_agent_loop(
        agent_name="TestAgent",
        system_prompt="你是一个测试 Agent。",
        tools=[],
        available_tools={
            "list_files": fake_list_files,
            "read_file": fake_read_file,
        },
        user_message="请分析 demo_project/main.py",
        max_steps=3,
    )

    assert result["status"] == "finished"

    step_numbers = [
        step["step"]
        for step in result["steps"]
    ]

    assert step_numbers == [1, 2, 3]
    assert len(step_numbers) == len(set(step_numbers))

    assert result["steps"][0]["model_round"] == 1
    assert result["steps"][1]["model_round"] == 1
    assert result["steps"][2]["model_round"] == 2

    assert result["steps"][0]["tool_name"] == "list_files"
    assert result["steps"][1]["tool_name"] == "read_file"
    assert result["steps"][2]["type"] == "final_answer"