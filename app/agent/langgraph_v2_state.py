from __future__ import annotations

from operator import add
from typing import Annotated, Any
from typing_extensions import TypedDict


LANGGRAPH_V2_WORKFLOW_VERSION = "2.0"


class FineGrainedCodingGraphState(TypedDict, total=False):
    """Day15 细粒度编排状态。

    v2 与 v1 的关键差异：
    - messages / steps / graph_events 使用 add reducer，
      节点只能返回“新增的增量”，不能返回完整旧列表；
    - executor_state 直接成为图状态，
      由 plan 创建、execute_tool / approval 等节点返回更新后的完整 dict；
    - pending_tool_calls + tool_cursor 驱动“一次处理一个 tool_call”；
    - gate_exit 是 gate 节点（tool_gate / executor_gate / policy）
      写出的瞬时路由标记，由对应的 route_after_* 读取。
    """

    thread_id: str
    user_message: str
    max_steps: int

    task_plan: dict[str, Any]
    executor_state: dict[str, Any] | None
    messages: Annotated[list[dict[str, Any]], add]
    steps: Annotated[list[dict[str, Any]], add]

    model_round: int
    step_counter: int
    reflection_retry_count: int
    premature_final_count: int

    pending_tool_calls: list[dict[str, Any]]
    tool_cursor: int
    gate_exit: str
    tool_execution: dict[str, Any] | None

    # Tool Call Batch Barrier：同一条 assistant(tool_calls) 的 batch 尚未全部
    # 得到 role=tool 响应之前，reflection / 审批恢复等“非 tool feedback”只能
    # 暂存到这里，由 model_node 在 cursor >= len(pending) 且调用 LLM 前统一注入，
    # 否则会把 system/user/assistant 消息插进 tool responses 中间，
    # 违反 OpenAI Tool Calling 协议（insufficient tool messages）。
    # 普通 replace 字段：写入方返回 [*existing, new] 累积，model_node 消费后返回 [] 清空。
    deferred_feedback: list[dict[str, Any]]

    pending_action: dict[str, Any] | None
    approval_result: dict[str, Any] | None
    final_result: dict[str, Any] | None

    status: str
    answer: str
    error: dict[str, Any] | None
    graph_events: Annotated[list[dict[str, Any]], add]
