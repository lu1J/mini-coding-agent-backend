from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from app.agent.langgraph_state import (
    CodingGraphState,
    LANGGRAPH_WORKFLOW_VERSION,
    build_graph_event,
)
from app.agent.task_planner import build_task_plan


AgentRunner = Callable[[CodingGraphState], dict[str, Any]]
ApprovalExecutor = Callable[..., dict[str, Any]]


def _load_langgraph_symbols():
    """延迟导入，避免未安装 LangGraph 时破坏旧 /agent/code。"""
    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph 尚未安装。请执行："
            "python -m pip install langgraph==1.2.9 "
            "langgraph-checkpoint-sqlite==3.1.0"
        ) from exc

    return StateGraph, START, END, interrupt


def run_existing_code_agent(
    state: CodingGraphState,
) -> dict[str, Any]:
    """复用当前已经验证过的 Planner–Executor Tool Loop。"""
    from app.agent.agent_loop import run_agent_loop
    from app.agent.code_agent import (
        AVAILABLE_CODE_AGENT_TOOLS,
        CODE_AGENT_SYSTEM_PROMPT,
        CODE_AGENT_TOOLS,
    )

    return run_agent_loop(
        agent_name="CodeAgent",
        system_prompt=CODE_AGENT_SYSTEM_PROMPT,
        tools=CODE_AGENT_TOOLS,
        available_tools=AVAILABLE_CODE_AGENT_TOOLS,
        user_message=str(state.get("user_message") or ""),
        max_steps=int(state.get("max_steps") or 8),
        task_plan_override=deepcopy(state.get("task_plan") or {}),
    )


def execute_existing_approval(
    *,
    approval_id: str,
    approved: bool,
) -> dict[str, Any]:
    from app.agent.verified_approval import execute_verified_approval

    return execute_verified_approval(
        approval_id=approval_id,
        approved=approved,
    )


def build_code_agent_graph(
    *,
    checkpointer: Any,
    agent_runner: AgentRunner | None = None,
    approval_executor: ApprovalExecutor | None = None,
):
    """构建 CodeAgent LangGraph v1。

    图只负责粗粒度生命周期：
        START -> plan -> execute -> [approval?] -> finalize -> END

    现有 Planner–Executor、Policy Guard、Verifier 均继续复用。
    """
    StateGraph, START, END, interrupt = _load_langgraph_symbols()
    agent_runner = agent_runner or run_existing_code_agent
    approval_executor = approval_executor or execute_existing_approval

    def plan_node(state: CodingGraphState) -> dict[str, Any]:
        task_plan = state.get("task_plan")
        if not isinstance(task_plan, dict) or not task_plan:
            task_plan = build_task_plan(
                str(state.get("user_message") or "")
            )

        return {
            "task_plan": task_plan,
            "status": "planned",
            "graph_events": [
                build_graph_event(
                    "plan_created",
                    workflow_version=LANGGRAPH_WORKFLOW_VERSION,
                    estimated_steps=task_plan.get("estimated_steps"),
                    risk_level=task_plan.get("risk_level"),
                )
            ],
        }

    def execute_node(state: CodingGraphState) -> dict[str, Any]:
        result = agent_runner(state)
        return {
            "agent_result": result,
            "status": str(result.get("status") or "failed"),
            "answer": str(result.get("answer") or ""),
            "executor_state": result.get("executor_state"),
            "pending_action": result.get("pending_action"),
            "error": result.get("error"),
            "graph_events": [
                build_graph_event(
                    "agent_execution_finished",
                    status=result.get("status"),
                )
            ],
        }

    def route_after_execute(state: CodingGraphState) -> str:
        if state.get("status") == "waiting_approval":
            return "approval"
        return "finalize"

    def approval_node(state: CodingGraphState) -> dict[str, Any]:
        pending_action = state.get("pending_action") or {}
        approval_id = str(pending_action.get("approval_id") or "")
        if not approval_id:
            return {
                "status": "failed",
                "error": {
                    "type": "missing_approval_id",
                    "message": "LangGraph 进入审批节点，但没有 approval_id。",
                },
                "graph_events": [
                    build_graph_event("approval_context_invalid")
                ],
            }

        decision = interrupt(
            {
                "type": "approval_required",
                "thread_id": state.get("thread_id"),
                "approval_id": approval_id,
                "tool_name": pending_action.get("tool_name"),
                "tool_args": pending_action.get("tool_args"),
                "risk_level": pending_action.get("risk_level"),
                "reason": pending_action.get("reason"),
            }
        )

        if isinstance(decision, dict):
            approved = bool(decision.get("approved"))
        else:
            approved = bool(decision)

        approval_result = approval_executor(
            approval_id=approval_id,
            approved=approved,
        )

        resume_result = approval_result.get("resume_result")
        if isinstance(resume_result, dict):
            status = str(resume_result.get("status") or "finished")
            answer = str(resume_result.get("answer") or "")
            executor_state = resume_result.get("executor_state")
            error = resume_result.get("error")
        else:
            status = str(approval_result.get("status") or "failed")
            answer = str(approval_result.get("message") or "")
            executor_state = state.get("executor_state")
            error = approval_result.get("error")

        return {
            "approval_result": approval_result,
            "status": status,
            "answer": answer,
            "executor_state": executor_state,
            "pending_action": None,
            "error": error,
            "graph_events": [
                build_graph_event(
                    "approval_resolved",
                    approved=approved,
                    approval_status=approval_result.get("status"),
                )
            ],
        }

    def finalize_node(state: CodingGraphState) -> dict[str, Any]:
        approval_result = state.get("approval_result")
        agent_result = state.get("agent_result") or {}

        if isinstance(approval_result, dict) and approval_result:
            resume_result = approval_result.get("resume_result")
            if isinstance(resume_result, dict):
                final_result = deepcopy(resume_result)
                final_result["verification_report"] = approval_result.get(
                    "verification_report"
                )
                final_result["approval_id"] = approval_result.get("approval_id")
            else:
                final_result = {
                    "status": state.get("status"),
                    "answer": state.get("answer", ""),
                    "error": state.get("error"),
                }
        else:
            final_result = deepcopy(agent_result)

        final_result["graph"] = {
            "workflow_version": LANGGRAPH_WORKFLOW_VERSION,
            "thread_id": state.get("thread_id"),
            "orchestrator": "langgraph",
        }

        return {
            "final_result": final_result,
            "graph_events": [
                build_graph_event(
                    "graph_finalized",
                    status=final_result.get("status"),
                )
            ],
        }

    builder = StateGraph(CodingGraphState)
    builder.add_node("plan", plan_node)
    builder.add_node("execute", execute_node)
    builder.add_node("approval", approval_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "execute")
    builder.add_conditional_edges(
        "execute",
        route_after_execute,
        {
            "approval": "approval",
            "finalize": "finalize",
        },
    )
    builder.add_edge("approval", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
