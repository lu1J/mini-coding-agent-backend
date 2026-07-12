import json
from pathlib import Path

from app.agent.run_logger import read_agent_run, save_agent_run


def test_save_agent_run_stores_task_plan():
    task_plan = {
        "objective": "请读取 demo_project/main.py",
        "intents": ["read"],
        "target_paths": ["demo_project/main.py"],
        "suggested_tools": ["list_files", "read_file"],
        "risk_level": "low",
        "complexity": "simple",
        "needs_approval": False,
        "estimated_steps": 1,
        "steps": [],
        "warnings": [],
    }

    log_info = save_agent_run(
        agent_name="TestAgent",
        user_message="请读取 demo_project/main.py",
        status="finished",
        answer="测试回答",
        steps=[],
        max_steps=3,
        model_name="test-model",
        task_plan=task_plan,
    )

    run_id = log_info["run_id"]
    saved_run = read_agent_run(run_id)

    assert saved_run["task_plan"] == task_plan
    assert saved_run["task_plan"]["objective"] == "请读取 demo_project/main.py"