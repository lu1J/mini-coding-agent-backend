from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.agent.langgraph_state import LANGGRAPH_WORKFLOW_VERSION
from app.agent.langgraph_workflow import build_code_agent_graph


DEFAULT_GRAPH_DB = Path("workspace") / ".agent_graph" / "checkpoints.sqlite3"


def new_thread_id() -> str:
    return "graph_" + uuid.uuid4().hex[:16]


def _load_sqlite_saver():
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise RuntimeError(
            "缺少 LangGraph SQLite Checkpointer。请执行："
            "python -m pip install langgraph==1.2.9 "
            "langgraph-checkpoint-sqlite==3.1.0"
        ) from exc
    return SqliteSaver


@contextmanager
def open_graph_checkpointer(
    db_path: str | Path = DEFAULT_GRAPH_DB,
) -> Iterator[Any]:
    """每次 API 调用打开一个 SQLite checkpointer，调用后安全关闭。"""
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        str(path),
        check_same_thread=False,
    )
    try:
        SqliteSaver = _load_sqlite_saver()
        saver = SqliteSaver(connection)
        setup = getattr(saver, "setup", None)
        if callable(setup):
            setup()
        yield saver
    finally:
        connection.close()


def _config(thread_id: str) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": thread_id,
        }
    }


def _serialize_interrupts(raw_result: dict[str, Any]) -> list[Any]:
    interrupts = raw_result.get("__interrupt__") or []
    serialized: list[Any] = []
    for item in interrupts:
        value = getattr(item, "value", item)
        serialized.append(value)
    return serialized


def _project_result(
    *,
    raw_result: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    interrupts = _serialize_interrupts(raw_result)

    if interrupts:
        state_result = raw_result.get("agent_result") or {}
        return {
            "status": "waiting_approval",
            "answer": str(state_result.get("answer") or "等待人工审批。"),
            "thread_id": thread_id,
            "graph_version": LANGGRAPH_WORKFLOW_VERSION,
            "task_plan": raw_result.get("task_plan"),
            "executor_state": raw_result.get("executor_state"),
            "pending_action": raw_result.get("pending_action"),
            "interrupts": interrupts,
            "graph_events": raw_result.get("graph_events", []),
        }

    final_result = raw_result.get("final_result")
    if isinstance(final_result, dict):
        result = dict(final_result)
    else:
        result = {
            "status": raw_result.get("status", "finished"),
            "answer": raw_result.get("answer", ""),
            "error": raw_result.get("error"),
        }

    result["thread_id"] = thread_id
    result["graph_version"] = LANGGRAPH_WORKFLOW_VERSION
    result["graph_events"] = raw_result.get("graph_events", [])
    return result


def run_code_agent_graph(
    *,
    user_message: str,
    max_steps: int = 8,
    thread_id: str | None = None,
    db_path: str | Path = DEFAULT_GRAPH_DB,
) -> dict[str, Any]:
    resolved_thread_id = thread_id or new_thread_id()

    with open_graph_checkpointer(db_path) as checkpointer:
        graph = build_code_agent_graph(checkpointer=checkpointer)
        raw_result = graph.invoke(
            {
                "thread_id": resolved_thread_id,
                "user_message": user_message,
                "max_steps": max_steps,
                "graph_events": [],
            },
            config=_config(resolved_thread_id),
        )

    return _project_result(
        raw_result=raw_result,
        thread_id=resolved_thread_id,
    )


def resume_code_agent_graph(
    *,
    thread_id: str,
    approved: bool,
    db_path: str | Path = DEFAULT_GRAPH_DB,
) -> dict[str, Any]:
    try:
        from langgraph.types import Command
    except ImportError as exc:
        raise RuntimeError("LangGraph 尚未安装。") from exc

    with open_graph_checkpointer(db_path) as checkpointer:
        graph = build_code_agent_graph(checkpointer=checkpointer)
        raw_result = graph.invoke(
            Command(
                resume={
                    "approved": approved,
                }
            ),
            config=_config(thread_id),
        )

    return _project_result(
        raw_result=raw_result,
        thread_id=thread_id,
    )


def get_code_agent_graph_state(
    *,
    thread_id: str,
    db_path: str | Path = DEFAULT_GRAPH_DB,
) -> dict[str, Any]:
    with open_graph_checkpointer(db_path) as checkpointer:
        graph = build_code_agent_graph(checkpointer=checkpointer)
        snapshot = graph.get_state(_config(thread_id))

    return {
        "thread_id": thread_id,
        "values": dict(snapshot.values or {}),
        "next": list(snapshot.next or ()),
        "metadata": dict(snapshot.metadata or {}),
        "checkpoint_id": (
            snapshot.config.get("configurable", {}).get("checkpoint_id")
            if snapshot.config
            else None
        ),
    }
