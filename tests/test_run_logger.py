from app.agent import run_logger
from app.agent.status import AGENT_STATUS_FINISHED, AGENT_STATUS_FAILED


def test_save_and_read_agent_run():
    """
    Agent 运行日志应该可以保存并读取。
    """
    log_info = run_logger.save_agent_run(
        agent_name="TestAgent",
        model_name="test-model",
        status=AGENT_STATUS_FINISHED,
        user_message="测试运行日志保存",
        answer="这是测试回答",
        max_steps=3,
        steps=[
            {
                "step": 1,
                "type": "tool_call",
                "tool_name": "read_file_lines",
                "tool_args": {
                    "path": "demo_project/main.py",
                    "start_line": 1,
                    "end_line": 5,
                },
                "risk_level": "low",
                "tool_result": "测试结果",
                "success": True,
                "error": None,
                "started_at": "2026-01-01T00:00:00",
                "ended_at": "2026-01-01T00:00:01",
                "duration_ms": 1000,
            }
        ],
        error=None,
        pending_action=None,
    )

    run_id = log_info["run_id"]

    loaded = run_logger.read_agent_run(run_id)

    assert loaded is not None
    assert loaded["run_id"] == run_id
    assert loaded["agent_name"] == "TestAgent"
    assert loaded["model_name"] == "test-model"
    assert loaded["status"] == AGENT_STATUS_FINISHED
    assert loaded["user_message"] == "测试运行日志保存"
    assert loaded["answer"] == "这是测试回答"
    assert len(loaded["steps"]) == 1
    assert loaded["steps"][0]["tool_name"] == "read_file_lines"


def test_list_agent_runs_contains_saved_run():
    """
    list_agent_runs 应该能列出刚保存的运行记录。
    """
    log_info = run_logger.save_agent_run(
        agent_name="TestAgent",
        model_name="test-model",
        status=AGENT_STATUS_FINISHED,
        user_message="测试运行记录列表",
        answer="列表测试回答",
        max_steps=2,
        steps=[],
        error=None,
        pending_action=None,
    )

    run_id = log_info["run_id"]

    runs = run_logger.list_agent_runs(limit=20)

    run_ids = [item["run_id"] for item in runs]

    assert run_id in run_ids


def test_save_failed_agent_run_with_error():
    """
    failed 状态的运行日志应该保存 error 信息。
    """
    error = {
        "type": "model_error",
        "message": "模型调用失败。",
        "detail": "Connection error.",
    }

    log_info = run_logger.save_agent_run(
        agent_name="TestAgent",
        model_name="test-model",
        status=AGENT_STATUS_FAILED,
        user_message="测试失败日志",
        answer="模型调用失败，任务已停止。",
        max_steps=3,
        steps=[],
        error=error,
        pending_action=None,
    )

    run_id = log_info["run_id"]

    loaded = run_logger.read_agent_run(run_id)

    assert loaded is not None
    assert loaded["status"] == AGENT_STATUS_FAILED
    assert loaded["error"]["type"] == "model_error"
    assert loaded["error"]["message"] == "模型调用失败。"


def test_invalid_run_id_returns_error():
    """
    非法 run_id 应该被拒绝。
    """
    try:
        run_logger.read_agent_run("../bad_run_id")
        assert False, "非法 run_id 应该抛出 ValueError"
    except ValueError:
        assert True