from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_agent_plan_api_builds_read_plan():
    response = client.post(
        "/agent/plan",
        json={
            "message": "请读取 demo_project/main.py 并告诉我内容",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["objective"] == "请读取 demo_project/main.py 并告诉我内容"
    assert "read" in data["intents"]
    assert "demo_project/main.py" in data["target_paths"]
    assert "read_file" in data["suggested_tools"]
    assert data["risk_level"] == "low"
    assert data["needs_approval"] is False
    assert data["estimated_steps"] >= 1


def test_agent_plan_api_builds_high_risk_edit_plan():
    response = client.post(
        "/agent/plan",
        json={
            "message": "请修改 demo_project/main.py，把返回内容改成 Hello",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert "edit" in data["intents"]
    assert "edit_file" in data["suggested_tools"]
    assert data["risk_level"] == "high"
    assert data["needs_approval"] is True

    step_tools = [
        step["suggested_tool"]
        for step in data["steps"]
    ]

    assert "read_file" in step_tools
    assert "edit_file" in step_tools
    assert "get_workspace_diff" in step_tools


def test_agent_plan_api_builds_medium_risk_test_plan():
    response = client.post(
        "/agent/plan",
        json={
            "message": "请运行 pytest 检查项目",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert "test" in data["intents"]
    assert "run_command" in data["suggested_tools"]
    assert data["risk_level"] == "medium"
    assert data["needs_approval"] is False


def test_agent_plan_api_accepts_empty_message():
    response = client.post(
        "/agent/plan",
        json={
            "message": "",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["objective"] == ""
    assert "analyze" in data["intents"]
    assert data["risk_level"] == "low"