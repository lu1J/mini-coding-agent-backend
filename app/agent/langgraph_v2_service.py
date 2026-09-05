from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.agent.langgraph_v2_state import LANGGRAPH_V2_WORKFLOW_VERSION
from app.agent.langgraph_v2_workflow import build_fine_grained_graph


DEFAULT_V2_GRAPH_DB = Path("workspace") / ".agent_graph" / "v2_checkpoints.sqlite3"


def new_v2_thread_id() -> str:
    """v2 独立 thread 前缀，与 v1 的 thread 命名空间隔离。"""
    return "v2_" + uuid.uuid4().hex[:16]


@contextmanager
def open_v2_graph_checkpointer(
    db_path: Path | str = DEFAULT_V2_GRAPH_DB,
) -> Iterator[Any]:
    """v2 使用独立的 SQLite Checkpointer，不污染 v1 的 checkpoint。"""
    import os

    from langgraph.checkpoint.sqlite import SqliteSaver

    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        str(path),
        check_same_thread=False,
    )
    try:
        saver = SqliteSaver(connection)
        setup = getattr(saver, "setup", None)
        if callable(setup):
            setup()
        yield saver
    finally:
        connection.close()


class V2ThreadNotFoundError(LookupError):
    """resume 目标 thread 在 v2 checkpoint 中不存在。

    Day20 P1：langgraph 1.x 对不存在的 thread 执行 Command(resume) 时，
    会把它当作全新输入跑一遍完整 graph（ghost run，约 14-42 秒），
    并顺带创建 checkpoint、真实执行工具。调用方应捕获本异常并快速
    返回 404，而不是进入那条慢路径。
    """

    def __init__(self, thread_id: str):
        super().__init__(f"找不到 v2 graph thread：{thread_id}")
        self.thread_id = thread_id


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _serialize_v2_interrupts(raw_result: dict[str, Any]) -> list[Any]:
    interrupts = raw_result.get("__interrupt__") or []
    serialized: list[Any] = []
    for item in interrupts:
        value = getattr(item, "value", item)
        serialized.append(value)
    return serialized


def _project_v2_result(raw_result: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """把图原始返回整理成 API 响应。"""
    interrupts = _serialize_v2_interrupts(raw_result)
    if interrupts:
        return {
            "status": "waiting_approval",
            "answer": "等待人工审批。",
            "thread_id": thread_id,
            "graph_version": LANGGRAPH_V2_WORKFLOW_VERSION,
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
            "status": raw_result.get("status") or "failed",
            "answer": raw_result.get("answer") or "",
            "error": raw_result.get("error"),
        }
    result["thread_id"] = thread_id
    result["graph_version"] = LANGGRAPH_V2_WORKFLOW_VERSION
    result["graph_events"] = raw_result.get("graph_events", [])
    return result


def _load_code_agent_runtime() -> dict[str, Any]:
    """读取 CodeAgent 运行时（系统提示词、工具 schema、工具实现）。"""
    from app.agent.code_agent import (
        AVAILABLE_CODE_AGENT_TOOLS,
        CODE_AGENT_SYSTEM_PROMPT,
        CODE_AGENT_TOOLS,
    )

    return {
        "system_prompt": CODE_AGENT_SYSTEM_PROMPT,
        "tools": CODE_AGENT_TOOLS,
        "available_tools": AVAILABLE_CODE_AGENT_TOOLS,
    }


def _initial_state(*, thread_id: str, user_message: str, max_steps: int) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "user_message": user_message,
        "max_steps": max_steps,
        "messages": [],
        "steps": [],
        "graph_events": [],
        "pending_tool_calls": [],
        "tool_cursor": 0,
        "deferred_feedback": [],
        "model_round": 0,
        "step_counter": 0,
        "reflection_retry_count": 0,
        "premature_final_count": 0,
    }


def run_fine_grained_graph(
    *,
    user_message: str,
    max_steps: int = 8,
    thread_id: str | None = None,
    db_path: Path | str = DEFAULT_V2_GRAPH_DB,
) -> dict[str, Any]:
    resolved_thread_id = thread_id or new_v2_thread_id()
    runtime = _load_code_agent_runtime()
    with open_v2_graph_checkpointer(db_path) as checkpointer:
        graph = build_fine_grained_graph(checkpointer=checkpointer, **runtime)
        raw_result = graph.invoke(
            _initial_state(
                thread_id=resolved_thread_id,
                user_message=user_message,
                max_steps=max_steps,
            ),
            config=_config(resolved_thread_id),
        )
    return _project_v2_result(raw_result, resolved_thread_id)


def resume_fine_grained_graph(
    *,
    thread_id: str,
    approved: bool,
    db_path: Path | str = DEFAULT_V2_GRAPH_DB,
) -> dict[str, Any]:
    from langgraph.types import Command  # 延迟导入 langgraph

    runtime = _load_code_agent_runtime()
    with open_v2_graph_checkpointer(db_path) as checkpointer:
        # Day20 P1：先探测 checkpoint 是否存在。thread 不存在时快速抛错，
        # 绝不进入 graph.invoke（langgraph 会把 ghost resume 当成全新 run，
        # 长时间执行完整 agent 并写入 checkpoint / 触发真实工具副作用）。
        existing_checkpoint = checkpointer.get_tuple(_config(thread_id))
        if existing_checkpoint is None:
            raise V2ThreadNotFoundError(thread_id)

        graph = build_fine_grained_graph(checkpointer=checkpointer, **runtime)
        raw_result = graph.invoke(
            Command(resume={"approved": approved}),
            config=_config(thread_id),
        )
    return _project_v2_result(raw_result, thread_id)


def get_fine_grained_graph_state(
    *,
    thread_id: str,
    db_path: Path | str = DEFAULT_V2_GRAPH_DB,
) -> dict[str, Any]:
    with open_v2_graph_checkpointer(db_path) as checkpointer:
        graph = build_fine_grained_graph(
            checkpointer=checkpointer,
            **_load_code_agent_runtime(),
        )
        state = graph.get_state(_config(thread_id))
    values = state.values if state is not None else {}
    return {
        "thread_id": thread_id,
        "graph_version": LANGGRAPH_V2_WORKFLOW_VERSION,
        "values": {
            key: values.get(key)
            for key in (
                "user_message",
                "max_steps",
                "task_plan",
                "executor_state",
                "status",
                "answer",
                "error",
                "pending_tool_calls",
                "tool_cursor",
                "pending_action",
                "approval_result",
                "final_result",
                "model_round",
                "step_counter",
                "reflection_retry_count",
                "premature_final_count",
            )
        },
        "next": state.next if state is not None else [],
    }
