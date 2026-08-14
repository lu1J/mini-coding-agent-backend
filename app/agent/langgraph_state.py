from __future__ import annotations

from operator import add
from typing import Annotated, Any
from typing_extensions import TypedDict


LANGGRAPH_WORKFLOW_VERSION = "1.0"


class CodingGraphState(TypedDict, total=False):
    """LangGraph 粗粒度编排状态。

    Day 14 v1 不替换已经稳定的 Planner–Executor 内部工具循环，
    而是把“规划 -> 执行 -> 人工审批 -> 最终返回”提升到图状态层。
    """

    thread_id: str
    user_message: str
    max_steps: int

    task_plan: dict[str, Any]
    agent_result: dict[str, Any]
    approval_result: dict[str, Any]
    final_result: dict[str, Any]

    status: str
    answer: str
    executor_state: dict[str, Any] | None
    pending_action: dict[str, Any] | None
    error: dict[str, Any] | None

    graph_events: Annotated[list[dict[str, Any]], add]


def build_graph_event(
    event: str,
    **payload: Any,
) -> dict[str, Any]:
    return {
        "event": event,
        **payload,
    }
