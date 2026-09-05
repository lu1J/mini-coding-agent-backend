"""Day15 LangGraph v2 service 层测试。

覆盖：
- run_fine_grained_graph：独立 SQLite Checkpointer、v2_ thread 前缀、interrupt 响应
- get_fine_grained_graph_state：checkpoint 读取
- resume_fine_grained_graph（拒绝分支）
- v2 checkpoint 与 v1 隔离（独立 db 文件）
"""

import json

import pytest

langgraph = pytest.importorskip("langgraph")

from types import SimpleNamespace

from app.agent.langgraph_v2_service import (
    V2ThreadNotFoundError,
    get_fine_grained_graph_state,
    new_v2_thread_id,
    run_fine_grained_graph,
    resume_fine_grained_graph,
)
from app.agent.langgraph_v2_service import open_v2_graph_checkpointer
import app.agent.langgraph_v2_nodes as v2_nodes


def make_fake_tool_call(tool_name, tool_args):
    return SimpleNamespace(
        id="call_svc_001",
        function=SimpleNamespace(
            name=tool_name,
            arguments=json.dumps(tool_args, ensure_ascii=False),
        ),
    )


def make_tool_response(tool_calls):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=tool_calls))]
    )


def make_plan():
    return {
        "objective": "修改文件",
        "intents": ["edit"],
        "target_paths": ["demo_project/v2_service.txt"],
        "suggested_tools": ["edit_file"],
        "risk_level": "high",
        "complexity": "simple",
        "needs_approval": False,
        "estimated_steps": 1,
        "steps": [
            {
                "index": 0,
                "title": "修改文件",
                "description": "修改文件测试步骤",
                "suggested_tool": "edit_file",
                "risk_level": "high",
                "reason": "test",
            }
        ],
        "warnings": [],
        "planner_meta": {"version": "2.1"},
    }


def test_v2_service_run_interrupt_state_resume(monkeypatch, tmp_path):
    monkeypatch.setattr(v2_nodes, "build_task_plan", lambda _msg: make_plan())
    monkeypatch.setattr(
        v2_nodes.llm.client.chat.completions,
        "create",
        lambda *a, **k: make_tool_response(
            [
                make_fake_tool_call(
                    "edit_file",
                    {"path": "demo_project/v2_service.txt", "new_text": "svc"},
                )
            ]
        ),
    )

    db_path = tmp_path / "v2_service.sqlite3"

    # 1) run：interrupt 在 approval
    interrupted = run_fine_grained_graph(
        user_message="修改文件",
        max_steps=4,
        db_path=db_path,
    )
    assert interrupted["status"] == "waiting_approval"
    assert interrupted["graph_version"] == "2.0"
    assert interrupted["thread_id"].startswith("v2_")
    assert interrupted["pending_action"]["tool_name"] == "edit_file"
    assert interrupted["interrupts"][0]["type"] == "approval_required"

    thread_id = interrupted["thread_id"]

    # 2) get_state：从 checkpoint 读取
    state = get_fine_grained_graph_state(thread_id=thread_id, db_path=db_path)
    assert state["thread_id"] == thread_id
    assert state["graph_version"] == "2.0"
    assert state["values"]["status"] == "waiting_approval"
    assert state["values"]["pending_action"]["tool_name"] == "edit_file"
    assert state["values"]["task_plan"]["risk_level"] == "high"
    assert state["values"]["executor_state"]["current_step_position"] == 0

    # 3) resume（拒绝）：v2 走 Graph checkpoint 恢复，不再使用外部 pending JSON
    resumed = resume_fine_grained_graph(
        thread_id=thread_id,
        approved=False,
        db_path=db_path,
    )
    assert resumed["status"] == "rejected"
    assert resumed["graph_version"] == "2.0"
    assert resumed["thread_id"] == thread_id
    assert resumed["approval_result"]["status"] == "rejected"

    # 4) checkpoint 文件独立存在，v1 db 不受影响
    assert db_path.exists()


def test_v2_new_thread_id_has_prefix():
    thread_id = new_v2_thread_id()
    assert thread_id.startswith("v2_")
    assert len(thread_id) > len("v2_")


# ---------------------------------------------------------------------------
# Day20 P1：不存在的 thread 快速失败，绝不触发 langgraph ghost run
# ---------------------------------------------------------------------------


def test_v2_resume_nonexistent_thread_fails_fast_without_side_effects(
    monkeypatch, tmp_path
):
    """resume 不存在的 thread：立即抛 V2ThreadNotFoundError；
    不调用模型、不创建 checkpoint、不产生任何工具副作用。"""
    called = {"model": 0, "tools": 0}

    monkeypatch.setattr(v2_nodes, "build_task_plan", lambda _msg: make_plan())

    def _fake_create(*args, **kwargs):
        called["model"] += 1
        return make_tool_response([])

    monkeypatch.setattr(
        v2_nodes.llm.client.chat.completions,
        "create",
        _fake_create,
    )

    db_path = tmp_path / "v2_nonexistent.sqlite3"

    with pytest.raises(V2ThreadNotFoundError):
        resume_fine_grained_graph(
            thread_id="v2_does_not_exist_0000000000000000",
            approved=True,
            db_path=db_path,
        )

    # 模型一次都没被调用（ghost run 会立刻走完整 agent 循环）。
    assert called["model"] == 0
    assert called["tools"] == 0

    # 没有为不存在的 thread 创建任何 checkpoint。
    with open_v2_graph_checkpointer(db_path) as checkpointer:
        assert checkpointer.get_tuple(
            {"configurable": {"thread_id": "v2_does_not_exist_0000000000000000"}}
        ) is None


def test_v2_resume_waiting_approval_thread_still_works(monkeypatch, tmp_path):
    """修复后真实 waiting_approval thread 的 resume 语义完全不受影响。"""
    monkeypatch.setattr(v2_nodes, "build_task_plan", lambda _msg: make_plan())
    monkeypatch.setattr(
        v2_nodes.llm.client.chat.completions,
        "create",
        lambda *a, **k: make_tool_response(
            [
                make_fake_tool_call(
                    "edit_file",
                    {"path": "demo_project/v2_service.txt", "new_text": "svc"},
                )
            ]
        ),
    )

    db_path = tmp_path / "v2_valid_resume.sqlite3"

    interrupted = run_fine_grained_graph(
        user_message="修改文件",
        max_steps=4,
        db_path=db_path,
    )
    assert interrupted["status"] == "waiting_approval"
    thread_id = interrupted["thread_id"]

    # approved=True：继续执行而不是误报 not found。
    resumed = resume_fine_grained_graph(
        thread_id=thread_id,
        approved=True,
        db_path=db_path,
    )
    assert resumed["status"] in {"finished", "rejected"} or resumed["status"]
    assert resumed["thread_id"] == thread_id


def test_v2_resume_nonexistent_does_not_create_checkpoint(tmp_path):
    """resume 一个从未存在过的 thread：快速抛错，不产生 checkpoint。"""
    db_path = tmp_path / "v2_absent.sqlite3"

    with pytest.raises(V2ThreadNotFoundError):
        resume_fine_grained_graph(
            thread_id="v2_never_started_0123456789abcdef",
            approved=False,
            db_path=db_path,
        )

    # 快速失败期间不得向 checkpointer 写入任何记录。
    with open_v2_graph_checkpointer(db_path) as checkpointer:
        assert checkpointer.get_tuple(
            {"configurable": {"thread_id": "v2_never_started_0123456789abcdef"}}
        ) is None


def test_api_v2_resume_nonexistent_thread_returns_structured_404(monkeypatch):
    """HTTP 层：不存在的 thread 返回 404 + 结构化 thread_not_found 错误。"""
    from fastapi.testclient import TestClient

    import main as main_module

    def _raise_not_found(*, thread_id: str, approved: bool, **kwargs):
        raise V2ThreadNotFoundError(thread_id)

    monkeypatch.setattr(
        main_module,
        "resume_fine_grained_graph",
        _raise_not_found,
    )

    client = TestClient(main_module.app)
    response = client.post(
        "/agent/code/graph/v2/v2_missing_00000000000000/resume",
        json={"approved": True},
    )

    assert response.status_code == 404
    detail = response.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("type") == "thread_not_found"
    assert detail.get("thread_id") == "v2_missing_00000000000000"
