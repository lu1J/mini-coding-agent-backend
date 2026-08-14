import pytest

langgraph = pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent.langgraph_workflow import build_code_agent_graph


def test_graph_finishes_without_approval(monkeypatch):
    monkeypatch.setattr(
        "app.agent.langgraph_workflow.build_task_plan",
        lambda _: {
            "objective": "读取文件",
            "estimated_steps": 1,
            "risk_level": "low",
        },
    )

    def fake_agent_runner(state):
        return {
            "status": "finished",
            "answer": "完成",
            "executor_state": {"status": "completed"},
            "steps": [],
        }

    graph = build_code_agent_graph(
        checkpointer=InMemorySaver(),
        agent_runner=fake_agent_runner,
    )
    config = {"configurable": {"thread_id": "test-finish"}}
    result = graph.invoke(
        {
            "thread_id": "test-finish",
            "user_message": "读取文件",
            "max_steps": 3,
            "graph_events": [],
        },
        config=config,
    )

    assert result["final_result"]["status"] == "finished"
    assert result["final_result"]["answer"] == "完成"
    assert result["final_result"]["graph"]["orchestrator"] == "langgraph"


def test_graph_interrupts_and_resumes_approval(monkeypatch):
    monkeypatch.setattr(
        "app.agent.langgraph_workflow.build_task_plan",
        lambda _: {
            "objective": "修改文件",
            "estimated_steps": 2,
            "risk_level": "high",
        },
    )

    def fake_agent_runner(state):
        return {
            "status": "waiting_approval",
            "answer": "等待审批",
            "executor_state": {"status": "waiting_approval"},
            "pending_action": {
                "approval_id": "approval_test",
                "tool_name": "edit_file",
                "tool_args": {"path": "demo.py"},
                "risk_level": "high",
            },
            "steps": [],
        }

    def fake_approval_executor(*, approval_id, approved):
        assert approval_id == "approval_test"
        assert approved is True
        return {
            "status": "approved",
            "approval_id": approval_id,
            "verification_report": {"success": True},
            "resume_result": {
                "status": "finished",
                "answer": "修改完成",
                "executor_state": {"status": "completed"},
                "steps": [],
            },
        }

    graph = build_code_agent_graph(
        checkpointer=InMemorySaver(),
        agent_runner=fake_agent_runner,
        approval_executor=fake_approval_executor,
    )
    config = {"configurable": {"thread_id": "test-approval"}}

    interrupted = graph.invoke(
        {
            "thread_id": "test-approval",
            "user_message": "修改文件",
            "max_steps": 3,
            "graph_events": [],
        },
        config=config,
    )
    assert interrupted.get("__interrupt__")

    resumed = graph.invoke(
        Command(resume={"approved": True}),
        config=config,
    )
    assert resumed["final_result"]["status"] == "finished"
    assert resumed["final_result"]["answer"] == "修改完成"
    assert resumed["final_result"]["verification_report"]["success"] is True
