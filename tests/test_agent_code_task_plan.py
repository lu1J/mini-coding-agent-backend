from types import SimpleNamespace

import app.agent.agent_loop as agent_loop
from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def make_fake_response(message):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=message,
            )
        ]
    )


def test_agent_code_response_contains_task_plan(monkeypatch):
    final_message = SimpleNamespace(
        content="这是测试回答。",
        tool_calls=None,
    )

    real_build_task_plan = agent_loop.build_task_plan

    def fake_build_task_plan(user_message: str):
        plan = real_build_task_plan(user_message)

        # 这个测试只验证 HTTP 响应是否包含 task_plan，
        # 不负责测试 Planner–Executor 是否执行完整工具链。
        # 因此把运行步骤替换成一个最终总结步骤，
        # 允许模型测试桩直接返回最终回答。
        plan["steps"] = [
            {
                "index": 1,
                "title": "返回测试回答",
                "description": (
                    "该测试只验证 HTTP 响应会暴露 Task Plan。"
                ),
                "suggested_tool": None,
                "risk_level": "low",
                "reason": (
                    "避免测试桩在 Planner–Executor 下提前结束。"
                ),
            }
        ]
        plan["estimated_steps"] = 1

        return plan

    monkeypatch.setattr(
        agent_loop,
        "build_task_plan",
        fake_build_task_plan,
    )

    def fake_create(*args, **kwargs):
        return make_fake_response(final_message)

    monkeypatch.setattr(
        agent_loop.llm.client.chat.completions,
        "create",
        fake_create,
    )

    response = client.post(
        "/agent/code",
        json={
            "message": "请修改 demo_project/main.py，把返回内容改成 Hello，并运行测试",
            "max_steps": 3,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "finished"
    assert data["task_plan"] is not None

    task_plan = data["task_plan"]

    assert task_plan["objective"] == "请修改 demo_project/main.py，把返回内容改成 Hello，并运行测试"
    assert "edit" in task_plan["intents"]
    assert "test" in task_plan["intents"]
    assert "demo_project/main.py" in task_plan["target_paths"]
    assert task_plan["risk_level"] == "high"
    assert task_plan["needs_approval"] is True
    assert "edit_file" in task_plan["suggested_tools"]
    assert "run_command" in task_plan["suggested_tools"]