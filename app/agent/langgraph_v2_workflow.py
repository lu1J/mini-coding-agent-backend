from __future__ import annotations

from typing import Any, Callable

from app.agent.langgraph_v2_nodes import (
    create_langgraph_v2_nodes,
    route_after_approval,
    route_after_execute_tool,
    route_after_executor_gate,
    route_after_model,
    route_after_policy,
    route_after_tool_gate,
)
from app.agent.langgraph_v2_state import FineGrainedCodingGraphState


def build_fine_grained_graph(
    *,
    checkpointer: Any,
    system_prompt: str,
    tools: list[dict[str, Any]],
    available_tools: dict[str, Callable[..., Any]],
    max_reflection_retries: int = 1,
):
    """构建 Day15 细粒度编排图。

    拓扑（Shadow Mode v2）：
    START → plan → model ⇄ tool_gate → executor_gate → policy → execute_tool ⇄ reflection
                        │                                  │
                        │                                  └→ approval（interrupt/resume）
                        └→ finalize → END

    注意：v2 不把 run_agent_loop() 包进任何节点，
    Model / Executor / Policy / Tool / Reflection / Approval 全部由 Node / Edge 控制。
    """
    from langgraph.graph import END, START, StateGraph  # 延迟导入 langgraph

    nodes = create_langgraph_v2_nodes(
        system_prompt=system_prompt,
        tools=tools,
        available_tools=available_tools,
        max_reflection_retries=max_reflection_retries,
    )

    builder = StateGraph(FineGrainedCodingGraphState)
    for node_name, node_func in nodes.items():
        builder.add_node(node_name, node_func)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "model")

    builder.add_conditional_edges(
        "model",
        route_after_model,
        {"tool_gate": "tool_gate", "model": "model", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "tool_gate",
        route_after_tool_gate,
        {
            "executor_gate": "executor_gate",
            "model": "model",
            "reflection": "reflection",
        },
    )
    builder.add_conditional_edges(
        "executor_gate",
        route_after_executor_gate,
        {"policy": "policy", "model": "model"},
    )
    builder.add_conditional_edges(
        "policy",
        route_after_policy,
        {"execute_tool": "execute_tool", "approval": "approval", "model": "model"},
    )
    builder.add_conditional_edges(
        "execute_tool",
        route_after_execute_tool,
        {"model": "model", "reflection": "reflection"},
    )
    builder.add_edge("reflection", "model")
    builder.add_conditional_edges(
        "approval",
        route_after_approval,
        {"model": "model", "finalize": "finalize"},
    )
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
