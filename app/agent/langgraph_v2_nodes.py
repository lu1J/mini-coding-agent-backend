from __future__ import annotations

import time
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Callable

from app.agent.agent_loop import (
    _executor_feedback,
    build_clean_tool_calls,
    build_invalid_argument_policy_decision,
    build_result_with_log,
    duration_ms,
    execute_tool as execute_tool_function,
    is_failed_tool_result,
    now_iso,
    parse_tool_arguments,
)
from app.agent.approval_store import create_approval_id
from app.agent.change_verifier import execute_verified_change
from app.agent.execution_policy_guard import (
    POLICY_BLOCK,
    POLICY_REQUIRE_APPROVAL,
    evaluate_tool_policy,
    format_policy_feedback,
)
from app.agent.langgraph_state import build_graph_event
from app.agent.langgraph_v2_state import (
    LANGGRAPH_V2_WORKFLOW_VERSION,
    FineGrainedCodingGraphState,
)
from app.agent.plan_executor import (
    EXECUTOR_ALLOW_SUPPORTING,
    build_executor_instruction,
    can_accept_degraded_final_answer,
    can_accept_final_answer,
    create_executor_state,
    evaluate_executor_tool_call,
    get_current_executor_step,
    mark_approved_step_completed,
    mark_current_step_result,
    mark_current_step_waiting_approval,
    mark_degraded_final_answer_completed,
    mark_executor_max_steps,
    mark_final_answer_completed,
    mark_supporting_tool_used,
    record_executor_block,
)
from app.agent.reflection import (
    build_max_steps_reflection,
    build_tool_error_reflection,
    should_retry_from_reflection,
)
from app.agent.status import (
    AGENT_STATUS_FAILED,
    AGENT_STATUS_FINISHED,
    AGENT_STATUS_MAX_STEPS_REACHED,
    AGENT_STATUS_REJECTED,
    AGENT_STATUS_WAITING_APPROVAL,
    ERROR_TYPE_MODEL,
    ERROR_TYPE_TOOL,
)
from app.agent.task_planner import build_task_plan
from app.agent.tool_policy import get_tool_risk_level
from app.agent.verified_approval import (
    _build_approved_tool_step,
    _should_run_tests,
)
from app.llm.deepseek_client import llm


def _tool_call_object(entry: dict[str, Any]) -> SimpleNamespace:
    """把 State 中保存的 tool call dict 还原成 agent_loop 期望的对象形状。"""
    function = entry.get("function") or {}
    return SimpleNamespace(
        id=entry.get("id", ""),
        function=SimpleNamespace(
            name=function.get("name", ""),
            arguments=function.get("arguments", "{}"),
        ),
    )


def _entry_tool_name(entry: dict[str, Any]) -> str:
    return str((entry.get("function") or {}).get("name") or "")


def _record(
    state: FineGrainedCodingGraphState,
    step_type: str,
    model_round: int,
    **fields: Any,
) -> tuple[int, dict[str, Any]]:
    """构造一条新的步骤记录（steps 使用 add reducer，只返回增量）。"""
    counter = int(state.get("step_counter") or 0) + 1
    record: dict[str, Any] = {
        "step": counter,
        "model_round": model_round,
        "type": step_type,
        "started_at": now_iso(),
        "ended_at": now_iso(),
        "duration_ms": 0,
        **fields,
    }
    return counter, record


def _step_position(current: dict[str, Any] | None) -> dict[str, Any]:
    if not current:
        return {"plan_step_index": None, "plan_step_title": "最终总结"}
    return {
        "plan_step_index": current.get("plan_step_index"),
        "plan_step_title": current.get("title"),
    }


def create_langgraph_v2_nodes(
    *,
    system_prompt: str,
    tools: list[dict[str, Any]],
    available_tools: dict[str, Callable[..., Any]],
    max_reflection_retries: int = 1,
    agent_name: str = "CodeAgent",
) -> dict[str, Callable[[FineGrainedCodingGraphState], dict[str, Any]]]:
    """创建 v2 细粒度图的全部节点（闭包注入运行时依赖）。"""

    # ------------------------------------------------------------------ plan
    def plan_node(state: FineGrainedCodingGraphState) -> dict[str, Any]:
        user_message = str(state.get("user_message") or "")
        task_plan = build_task_plan(user_message)
        executor_state = create_executor_state(task_plan)
        return {
            "task_plan": task_plan,
            "executor_state": executor_state,
            "status": "planned",
            "answer": "",
            "graph_events": [
                build_graph_event(
                    "plan_created",
                    workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                    risk_level=task_plan.get("risk_level"),
                    estimated_steps=task_plan.get("estimated_steps"),
                )
            ],
        }

    # ----------------------------------------------------------------- model
    def model_node(state: FineGrainedCodingGraphState) -> dict[str, Any]:
        # 本轮还有未处理的 tool_call，不调用模型，继续处理剩余调用。
        pending = state.get("pending_tool_calls") or []
        cursor = int(state.get("tool_cursor") or 0)
        if cursor < len(pending):
            return {}

        model_round = int(state.get("model_round") or 0)
        max_steps = int(state.get("max_steps") or 8)
        user_message = str(state.get("user_message") or "")
        executor_state = deepcopy(state.get("executor_state") or {})
        message_log = list(state.get("messages") or [])

        # 达到最大模型轮次：记录失败自省并停止。
        if model_round >= max_steps:
            reflection = build_max_steps_reflection(
                max_steps=max_steps,
                completed_steps=int(state.get("step_counter") or 0),
                user_message=user_message,
            )
            mark_executor_max_steps(executor_state)
            counter, record = _record(
                state,
                "reflection",
                model_round,
                content="达到最大模型轮次，生成失败自省结果。",
                success=False,
                error={
                    "type": "max_steps_reached",
                    "message": "达到最大模型轮次。",
                    "detail": None,
                },
                reflection=reflection,
                retry=False,
            )
            return {
                "executor_state": executor_state,
                "status": AGENT_STATUS_MAX_STEPS_REACHED,
                "answer": f"{agent_name} 达到最大模型轮次，Planner–Executor 已停止。",
                "steps": [record],
                "step_counter": counter,
                "graph_events": [
                    build_graph_event(
                        "max_steps_reached",
                        workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                        model_round=model_round,
                    )
                ],
            }

        # 与 v1 对齐：系统提示 + 原始 user_message 必须在模型上下文最前，
        # 否则模型只能靠步骤描述猜测用户意图（例如把“新增注释”误解为
        # “替换已有注释”，生成语法合法但语义错误的 edit 参数）。
        # Tool Call Batch Barrier：能走到这里说明 cursor >= len(pending)，
        # 本 batch 所有 tool_call_id 都已有 role=tool 响应；deferred feedback
        # （失败自省 / 审批后恢复上下文）此时注入请求，位置合法。
        deferred_feedback = list(state.get("deferred_feedback") or [])
        request_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
            *message_log,
            *deferred_feedback,
            {"role": "system", "content": build_executor_instruction(executor_state)},
        ]
        try:
            response = llm.client.chat.completions.create(
                model=llm.model,
                messages=request_messages,
                tools=tools,
                tool_choice="auto",
            )
        except Exception as error_value:  # noqa: BLE001 - 模型异常统一终止
            error = {
                "type": ERROR_TYPE_MODEL,
                "message": "模型调用失败，任务已停止。",
                "detail": str(error_value),
            }
            counter, record = _record(
                state,
                "model_call",
                model_round + 1,
                success=False,
                error=error,
                content="模型调用失败。",
            )
            return {
                "status": AGENT_STATUS_FAILED,
                "answer": "模型调用失败，任务已停止。",
                "error": error,
                "steps": [record],
                "step_counter": counter,
                "model_round": model_round + 1,
                "deferred_feedback": [],
                "graph_events": [
                    build_graph_event(
                        "model_error",
                        workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                    )
                ],
            }

        assistant_message = response.choices[0].message
        next_round = model_round + 1

        # 模型没有工具调用：final / degraded / premature final。
        if not assistant_message.tool_calls:
            final_answer = assistant_message.content or ""
            current = get_current_executor_step(executor_state)

            if can_accept_final_answer(executor_state):
                mark_final_answer_completed(executor_state)
                after = get_current_executor_step(executor_state)
                position = _step_position(after)
                counter, record = _record(
                    state,
                    "final_answer",
                    next_round,
                    content=final_answer,
                    success=True,
                    error=None,
                    degraded=False,
                    **position,
                )
                return {
                    "executor_state": executor_state,
                    "status": AGENT_STATUS_FINISHED,
                    "answer": final_answer,
                    # deferred feedback 本轮已注入请求；任务接受即持久化到
                    # messages 保持审计完整，并清空 State。
                    "messages": [*deferred_feedback],
                    "steps": [record],
                    "step_counter": counter,
                    "model_round": next_round,
                    "deferred_feedback": [],
                    "graph_events": [
                        build_graph_event(
                            "final_answer_accepted",
                            workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                            model_round=next_round,
                        )
                    ],
                }

            if can_accept_degraded_final_answer(executor_state):
                position = _step_position(current)
                mark_degraded_final_answer_completed(executor_state)
                counter, record = _record(
                    state,
                    "final_answer",
                    next_round,
                    content=final_answer,
                    success=True,
                    error=None,
                    degraded=True,
                    **position,
                )
                return {
                    "executor_state": executor_state,
                    "status": AGENT_STATUS_FINISHED,
                    "answer": final_answer,
                    # deferred feedback 本轮已注入请求；任务接受即持久化到
                    # messages 保持审计完整，并清空 State。
                    "messages": [*deferred_feedback],
                    "steps": [record],
                    "step_counter": counter,
                    "model_round": next_round,
                    "deferred_feedback": [],
                    "graph_events": [
                        build_graph_event(
                            "degraded_final_answer_accepted",
                            workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                            model_round=next_round,
                        )
                    ],
                }

            # premature final：计划未完成时尝试结束任务。
            premature_final_count = int(state.get("premature_final_count") or 0) + 1
            position = _step_position(current)
            feedback = (
                "[Planner–Executor]\n当前计划步骤尚未完成，暂时不能结束任务。\n"
                f"当前步骤：{position['plan_step_index']} - {position['plan_step_title']}\n"
                f"请调用：{current.get('suggested_tool') if current else '计划中的下一个工具'}"
            )
            counter, record = _record(
                state,
                "executor_blocked",
                next_round,
                content=final_answer,
                success=False,
                error={
                    "type": "premature_final_answer",
                    "message": "模型在计划完成前尝试结束任务。",
                    "detail": feedback,
                },
                **position,
            )
            new_messages = [
                {"role": "assistant", "content": final_answer},
                {"role": "user", "content": feedback},
            ]
            if premature_final_count >= 2:
                return {
                    "status": AGENT_STATUS_FAILED,
                    "answer": "模型连续两次提前结束，Planner–Executor 已停止任务。",
                    "error": {
                        "type": "executor_stalled",
                        "message": "模型连续两次提前结束任务。",
                        "detail": feedback,
                    },
                    # 持久化不变式：deferred 属于本轮请求前的历史，排在响应之前
                    "messages": [*deferred_feedback, *new_messages],
                    "steps": [record],
                    "step_counter": counter,
                    "premature_final_count": premature_final_count,
                    "model_round": next_round,
                    "deferred_feedback": [],
                    "graph_events": [
                        build_graph_event(
                            "premature_final_stalled",
                            workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                        )
                    ],
                }
            return {
                "executor_state": executor_state,
                # 持久化不变式：deferred 属于本轮请求前的历史，排在响应之前；
                # 持久化后清空 State，保证下一轮 message_log 完整、且不重复注入。
                "messages": [*deferred_feedback, *new_messages],
                "steps": [record],
                "step_counter": counter,
                "premature_final_count": premature_final_count,
                "model_round": next_round,
                "deferred_feedback": [],
                "graph_events": [
                    build_graph_event(
                        "premature_final_recovered",
                        workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                    )
                ],
            }

        # 模型返回工具调用：保存到 State，由 tool_gate 逐个处理。
        clean_tool_calls = build_clean_tool_calls(assistant_message.tool_calls)
        assistant_message_record = {
            "role": "assistant",
            "content": assistant_message.content or "",
            "tool_calls": clean_tool_calls,
        }
        # 持久化不变式：本轮请求是 old_history → deferred → instruction → LLM，
        # 则持久化增量必须是 [*deferred_feedback, assistant_message_record]——
        # deferred 属于“本轮请求之前的历史上下文”，必须排在模型响应之前。
        # 如果反序为 [assistant, *deferred]，checkpoint 会变成
        # assistant(tool_calls) → system(...)，下一次 LLM 调用必然 400。
        # （executor instruction 是 ephemeral，不持久化。）
        return {
            "messages": [*deferred_feedback, assistant_message_record],
            "pending_tool_calls": clean_tool_calls,
            "tool_cursor": 0,
            "model_round": next_round,
            "deferred_feedback": [],
        }

    # ------------------------------------------------------------- tool_gate
    def tool_gate_node(state: FineGrainedCodingGraphState) -> dict[str, Any]:
        pending = list(state.get("pending_tool_calls") or [])
        cursor = int(state.get("tool_cursor") or 0)
        if cursor >= len(pending):
            return {"gate_exit": "ok"}

        entry = pending[cursor]
        tool_name = _entry_tool_name(entry)
        risk_level = get_tool_risk_level(tool_name)
        model_round = int(state.get("model_round") or 0)
        current = get_current_executor_step(state.get("executor_state") or {})
        position = _step_position(current)

        tool_args, argument_error = parse_tool_arguments(_tool_call_object(entry))
        if argument_error:
            # 参数无法解析：按 v1 语义构造 invalid-argument 策略决策并反馈。
            executor_decision = evaluate_executor_tool_call(
                executor_state=state.get("executor_state") or {},
                tool_name=tool_name,
                risk_level=risk_level,
                tool_args=tool_args or {},
            )
            policy_decision = build_invalid_argument_policy_decision(
                tool_name=tool_name,
                risk_level=risk_level,
                error=argument_error,
            )
            feedback = format_policy_feedback(policy_decision)
            counter, record = _record(
                state,
                "policy_blocked",
                model_round,
                tool_name=tool_name,
                tool_args={},
                risk_level=risk_level,
                tool_result=feedback,
                content="工具参数无效，执行前策略已拦截。",
                success=False,
                error=argument_error,
                policy_decision=policy_decision,
                executor_decision=executor_decision,
                **position,
            )
            return {
                "steps": [record],
                "step_counter": counter,
                "tool_cursor": cursor + 1,
                "gate_exit": "args_error",
                "messages": [
                    {
                        "role": "tool",
                        "tool_call_id": entry.get("id", ""),
                        "content": feedback,
                    }
                ],
            }

        tool_func = available_tools.get(tool_name)
        if not tool_func:
            # 工具不存在：构造失败执行结果，交给 reflection 节点。
            execution = {
                "tool_call_id": entry.get("id", ""),
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_result": f"错误：{agent_name} 没有可用工具：{tool_name}",
                "success": False,
                "error": {
                    "type": ERROR_TYPE_TOOL,
                    "message": f"{agent_name} 没有可用工具：{tool_name}",
                    "detail": None,
                },
            }
            counter, record = _record(
                state,
                "tool_call",
                model_round,
                tool_name=tool_name,
                tool_args=tool_args,
                risk_level=risk_level,
                tool_result=execution["tool_result"],
                success=False,
                error=execution["error"],
                reflection=None,
                retry_from_reflection=False,
                policy_decision=None,
                executor_decision=None,
                **position,
            )
            return {
                "tool_execution": execution,
                "steps": [record],
                "step_counter": counter,
                "tool_cursor": cursor + 1,
                "gate_exit": "tool_missing",
                "messages": [
                    {
                        "role": "tool",
                        "tool_call_id": entry.get("id", ""),
                        "content": execution["tool_result"],
                    }
                ],
            }

        # 参数合法且工具存在：把解析结果回写到 pending_tool_calls。
        entry = deepcopy(entry)
        entry["parsed_args"] = tool_args
        pending[cursor] = entry
        return {"pending_tool_calls": pending, "gate_exit": "ok"}

    # --------------------------------------------------------- executor_gate
    def executor_gate_node(state: FineGrainedCodingGraphState) -> dict[str, Any]:
        pending = state.get("pending_tool_calls") or []
        cursor = int(state.get("tool_cursor") or 0)
        entry = pending[cursor]
        tool_name = _entry_tool_name(entry)
        tool_args = entry.get("parsed_args") or {}
        risk_level = get_tool_risk_level(tool_name)
        model_round = int(state.get("model_round") or 0)

        executor_state = deepcopy(state.get("executor_state") or {})
        decision = evaluate_executor_tool_call(
            executor_state=executor_state,
            tool_name=tool_name,
            risk_level=risk_level,
            tool_args=tool_args,
        )
        if not decision["allowed"]:
            # 计划步骤顺序 / supporting 约束被违反：记录并反馈给模型。
            feedback = _executor_feedback(decision)
            record_executor_block(executor_state, decision)
            counter, record = _record(
                state,
                "executor_blocked",
                model_round,
                tool_name=tool_name,
                tool_args={},
                tool_result=feedback,
                risk_level=risk_level,
                success=False,
                error={
                    "type": "plan_step_violation",
                    "message": "工具调用不符合当前计划步骤。",
                    "detail": feedback,
                },
                executor_decision=decision,
                plan_step_index=decision.get("current_plan_step_index"),
                plan_step_title=decision.get("current_plan_step_title"),
            )
            return {
                "executor_state": executor_state,
                "steps": [record],
                "step_counter": counter,
                "tool_cursor": cursor + 1,
                "gate_exit": "blocked",
                "messages": [
                    {
                        "role": "tool",
                        "tool_call_id": entry.get("id", ""),
                        "content": feedback,
                    }
                ],
            }
        return {"gate_exit": "allowed"}

    # ----------------------------------------------------------------- policy
    def policy_node(state: FineGrainedCodingGraphState) -> dict[str, Any]:
        pending = state.get("pending_tool_calls") or []
        cursor = int(state.get("tool_cursor") or 0)
        entry = pending[cursor]
        tool_name = _entry_tool_name(entry)
        tool_args = entry.get("parsed_args") or {}
        risk_level = get_tool_risk_level(tool_name)
        model_round = int(state.get("model_round") or 0)
        current = get_current_executor_step(state.get("executor_state") or {})
        position = _step_position(current)

        policy_decision = evaluate_tool_policy(
            task_plan=state.get("task_plan"),
            tool_name=tool_name,
            tool_args=tool_args,
            risk_level=risk_level,
        )

        if policy_decision["decision"] == POLICY_BLOCK:
            # 风险 / 计划范围 / 目标路径 / 命令安全被拦截：反馈给模型。
            feedback = format_policy_feedback(policy_decision)
            counter, record = _record(
                state,
                "policy_blocked",
                model_round,
                tool_name=tool_name,
                tool_args=tool_args,
                risk_level=risk_level,
                tool_result=feedback,
                content="工具未执行，模型可以根据策略反馈调整参数。",
                success=False,
                error={
                    "type": "policy_violation",
                    "message": "工具调用被 Execution Policy Guard 拦截。",
                    "detail": feedback,
                },
                policy_decision=policy_decision,
                **position,
            )
            return {
                "steps": [record],
                "step_counter": counter,
                "tool_cursor": cursor + 1,
                "gate_exit": "blocked",
                "messages": [
                    {
                        "role": "tool",
                        "tool_call_id": entry.get("id", ""),
                        "content": feedback,
                    }
                ],
            }

        if policy_decision["decision"] == POLICY_REQUIRE_APPROVAL:
            # 高风险写工具：把 pending_action 写入 Graph State，
            # 不推进 cursor（resume 成功后才推进，避免丢失后续 tool_call），
            # 也不做任何真实副作用，直接交给 approval 节点 interrupt()。
            executor_state = deepcopy(state.get("executor_state") or {})
            mark_current_step_waiting_approval(executor_state, tool_name=tool_name)
            pending_action = {
                "approval_id": create_approval_id(),
                "tool_call_id": entry.get("id", ""),
                "agent_name": agent_name,
                "user_message": str(state.get("user_message") or ""),
                "tool_name": tool_name,
                "tool_args": deepcopy(tool_args),
                "risk_level": risk_level,
                "reason": (
                    "Planner–Executor 已确认当前计划步骤；"
                    "Execution Policy Guard 已确认目标范围合法；"
                    f"工具 {tool_name} 仍需用户批准。"
                ),
                "plan_step_index": position["plan_step_index"],
                "plan_step_title": position["plan_step_title"],
            }
            counter, record = _record(
                state,
                "approval_required",
                model_round,
                tool_name=tool_name,
                tool_args=deepcopy(tool_args),
                risk_level=risk_level,
                tool_result="当前计划步骤与目标范围检查通过，高风险工具尚未执行。",
                content="等待用户确认后继续当前计划。",
                success=None,
                error=None,
                policy_decision=policy_decision,
                **position,
            )
            return {
                "executor_state": executor_state,
                "pending_action": pending_action,
                "steps": [record],
                "step_counter": counter,
                "gate_exit": "require_approval",
                "status": AGENT_STATUS_WAITING_APPROVAL,
            }

        return {"gate_exit": "allowed"}

    # ------------------------------------------------------------ execute_tool
    def execute_tool_node(state: FineGrainedCodingGraphState) -> dict[str, Any]:
        pending = state.get("pending_tool_calls") or []
        cursor = int(state.get("tool_cursor") or 0)
        entry = pending[cursor]
        tool_name = _entry_tool_name(entry)
        tool_args = entry.get("parsed_args") or {}
        risk_level = get_tool_risk_level(tool_name)
        model_round = int(state.get("model_round") or 0)

        executor_state = deepcopy(state.get("executor_state") or {})
        executor_decision = evaluate_executor_tool_call(
            executor_state=executor_state,
            tool_name=tool_name,
            risk_level=risk_level,
            tool_args=tool_args,
        )
        policy_decision = evaluate_tool_policy(
            task_plan=state.get("task_plan"),
            tool_name=tool_name,
            tool_args=tool_args,
            risk_level=risk_level,
        )
        current_before = get_current_executor_step(executor_state)

        started_at = now_iso()
        start_time = time.perf_counter()
        tool_execution = execute_tool_function(
            _tool_call_object(entry),
            available_tools,
            agent_name=agent_name,
            parsed_tool_args=tool_args,
        )
        elapsed_ms = duration_ms(start_time)
        tool_execution["started_at"] = started_at
        tool_execution["ended_at"] = now_iso()
        tool_execution["duration_ms"] = elapsed_ms

        # 依据 Executor 决策推进状态机：supporting 计数 或 完成/重试当前步骤。
        if executor_decision["decision"] == EXECUTOR_ALLOW_SUPPORTING:
            mark_supporting_tool_used(executor_state, tool_name=tool_name)
        elif executor_decision.get("completes_current_step"):
            mark_current_step_result(
                executor_state,
                tool_name=tool_name,
                success=bool(tool_execution["success"]),
                error=tool_execution.get("error"),
                tool_args=tool_args,
            )

        position = _step_position(current_before)
        counter, record = _record(
            state,
            "tool_call",
            model_round,
            tool_name=tool_name,
            tool_args=tool_execution.get("tool_args") or tool_args,
            risk_level=risk_level,
            tool_result=tool_execution["tool_result"],
            success=tool_execution["success"],
            error=tool_execution.get("error"),
            reflection=None,
            retry_from_reflection=False,
            policy_decision=policy_decision,
            executor_decision=executor_decision,
            **position,
        )
        return {
            "executor_state": executor_state,
            "tool_execution": tool_execution,
            "steps": [record],
            "step_counter": counter,
            "tool_cursor": cursor + 1,
            "messages": [
                {
                    "role": "tool",
                    "tool_call_id": tool_execution["tool_call_id"],
                    "content": tool_execution["tool_result"],
                }
            ],
        }

    # -------------------------------------------------------------- approval
    def approval_node(state: FineGrainedCodingGraphState) -> dict[str, Any]:
        from langgraph.types import interrupt  # 延迟导入，保持模块轻量

        pending_action = state.get("pending_action") or {}
        tool_name = str(pending_action.get("tool_name") or "")
        approval_id = str(pending_action.get("approval_id") or "")
        if not tool_name or not approval_id:
            return {
                "status": AGENT_STATUS_FAILED,
                "answer": "审批上下文不完整，任务停止。",
                "error": {
                    "type": "approval_context_invalid",
                    "message": "进入审批节点但缺少待审批工具或 approval_id。",
                    "detail": None,
                },
                "pending_action": None,
                "approval_result": {"status": "failed"},
                "graph_events": [
                    build_graph_event(
                        "approval_context_invalid",
                        workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                    )
                ],
            }

        decision = interrupt(
            {
                "type": "approval_required",
                "thread_id": state.get("thread_id"),
                "approval_id": approval_id,
                "tool_name": tool_name,
                "tool_args": pending_action.get("tool_args"),
                "risk_level": pending_action.get("risk_level"),
                "reason": pending_action.get("reason"),
                "plan_step_index": pending_action.get("plan_step_index"),
                "plan_step_title": pending_action.get("plan_step_title"),
            }
        )
        approved = (
            bool(decision.get("approved"))
            if isinstance(decision, dict)
            else bool(decision)
        )

        if not approved:
            # 拒绝：不产生任何文件副作用，直接结束。
            return {
                "status": AGENT_STATUS_REJECTED,
                "answer": "用户拒绝了高风险操作，任务停止。",
                "pending_action": None,
                "approval_result": {
                    "status": "rejected",
                    "approval_id": approval_id,
                    "tool_name": tool_name,
                },
                "graph_events": [
                    build_graph_event(
                        "approval_rejected",
                        workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                        approval_id=approval_id,
                        tool_name=tool_name,
                    )
                ],
            }

        # 批准：二次策略检查 → 事务化写操作 → 推进 Executor State。
        tool_args = pending_action.get("tool_args") or {}
        risk_level = str(pending_action.get("risk_level") or "high")
        task_plan = state.get("task_plan") or {}

        policy_decision = evaluate_tool_policy(
            task_plan=task_plan,
            tool_name=tool_name,
            tool_args=tool_args,
            risk_level=risk_level,
        )
        if policy_decision["decision"] == POLICY_BLOCK:
            feedback = format_policy_feedback(policy_decision)
            return {
                "status": "policy_blocked",
                "answer": "审批执行前的二次策略检查未通过，任务停止。",
                "error": {
                    "type": "policy_violation",
                    "message": "待确认动作已不符合当前执行策略。",
                    "detail": feedback,
                },
                "pending_action": None,
                "approval_result": {
                    "status": "policy_blocked",
                    "approval_id": approval_id,
                    "policy_decision": policy_decision,
                },
                "graph_events": [
                    build_graph_event(
                        "approval_policy_blocked",
                        workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                        approval_id=approval_id,
                    )
                ],
            }

        tool_func = available_tools.get(tool_name)
        if not tool_func:
            return {
                "status": AGENT_STATUS_FAILED,
                "answer": f"工具不存在：{tool_name}，任务停止。",
                "error": {
                    "type": ERROR_TYPE_TOOL,
                    "message": f"工具不存在：{tool_name}",
                    "detail": None,
                },
                "pending_action": None,
                "approval_result": {"status": "failed", "approval_id": approval_id},
                "graph_events": [
                    build_graph_event(
                        "approval_tool_missing",
                        workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                        approval_id=approval_id,
                    )
                ],
            }

        verification_report = execute_verified_change(
            tool_name=tool_name,
            tool_args=tool_args,
            tool_func=tool_func,
            run_tests=_should_run_tests(task_plan),
        )

        if not verification_report.get("success"):
            rollback_ok = (verification_report.get("rollback") or {}).get("success")
            return {
                "status": "verification_failed",
                "answer": (
                    "写操作未通过验证，系统已自动恢复修改前状态。"
                    if rollback_ok
                    else "写操作未通过验证，并且自动恢复未完全成功，需要人工检查。"
                ),
                "error": {
                    "type": "verification_failed",
                    "message": "写操作未通过修改后验证。",
                    "detail": verification_report.get("status"),
                },
                "pending_action": None,
                "approval_result": {
                    "status": "verification_failed",
                    "approval_id": approval_id,
                    "verification_report": verification_report,
                },
                "graph_events": [
                    build_graph_event(
                        "approval_verification_failed",
                        workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                        approval_id=approval_id,
                    )
                ],
            }

        executor_state = deepcopy(state.get("executor_state") or {})
        mark_approved_step_completed(executor_state, tool_name=tool_name)

        tool_execution = verification_report.get("tool_execution") or {}
        approved_step = _build_approved_tool_step(
            existing_steps=state.get("steps") or [],
            tool_name=tool_name,
            tool_args=tool_args,
            risk_level=risk_level,
            tool_execution=tool_execution,
            verification_report=verification_report,
            policy_decision=policy_decision,
        )
        counter = int(state.get("step_counter") or 0) + 1
        approved_step["step"] = counter

        cursor = int(state.get("tool_cursor") or 0)
        tool_call_id = str(pending_action.get("tool_call_id") or "")
        # 协议要求：assistant(tool_calls) 之后必须先有对应 tool message。
        # 必须先追加 role=tool（原始 tool_call_id + 真实执行结果），
        # 再追加恢复上下文 system 消息，否则下一次 LLM 调用会返回 400。
        tool_result_text = str(
            (verification_report.get("tool_execution") or {}).get("result") or ""
        )
        resume_context = (
            "[审批后恢复上下文]\n"
            "高风险写步骤已经通过用户审批、二次策略检查和自动验证。\n"
            f"已完成工具：{tool_name}\n"
            f"真实变更路径：{verification_report.get('changed_paths', [])}\n"
            f"验证状态：{verification_report.get('status')}\n"
            "请从 Executor State 的下一计划步骤继续；禁止重新调用已经完成的写工具。"
        )
        return {
            "executor_state": executor_state,
            "pending_action": None,
            "approval_result": {
                "status": "approved",
                "approval_id": approval_id,
                "verification_report": verification_report,
                "policy_decision": policy_decision,
            },
            "steps": [approved_step],
            "step_counter": counter,
            "tool_cursor": cursor + 1,
            # Tool Call Batch Barrier：tool message 直接追加（协议要求 role=tool
            # 紧跟 assistant tool_calls）；审批恢复上下文属于“非 tool feedback”，
            # 暂存到 deferred_feedback，等本 batch 所有 tool response 完成、
            # 调用 LLM 前由 model_node 注入，避免插进剩余 tool responses 中间。
            "messages": [
                {"role": "tool", "tool_call_id": tool_call_id, "content": tool_result_text},
            ],
            "deferred_feedback": [
                *list(state.get("deferred_feedback") or []),
                {"role": "system", "content": resume_context},
            ],
            "graph_events": [
                build_graph_event(
                    "approval_resolved",
                    workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                    approval_id=approval_id,
                    approved=True,
                    verification_status=verification_report.get("status"),
                )
            ],
        }

    # ------------------------------------------------------------- reflection
    def reflection_node(state: FineGrainedCodingGraphState) -> dict[str, Any]:
        execution = state.get("tool_execution") or {}
        tool_name = str(execution.get("tool_name") or "")
        reflection = build_tool_error_reflection(
            tool_name=tool_name,
            tool_args=execution.get("tool_args"),
            tool_result=execution,
            error=execution.get("error"),
        )
        retry_count = int(state.get("reflection_retry_count") or 0)
        retry = should_retry_from_reflection(
            reflection=reflection,
            retry_count=retry_count,
            max_retries=max_reflection_retries,
        )
        model_round = int(state.get("model_round") or 0)

        counter, record = _record(
            state,
            "reflection",
            model_round,
            content="工具失败自省。",
            success=False,
            error={
                "type": "tool_error_reflection",
                "message": "工具执行失败，已生成失败自省。",
                "detail": reflection.get("analysis"),
            },
            reflection=reflection,
            retry=retry,
        )
        result: dict[str, Any] = {
            "steps": [record],
            "step_counter": counter,
            "graph_events": [
                build_graph_event(
                    "reflection_created",
                    workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                    failed_tool=tool_name,
                    retry=retry,
                )
            ],
        }
        if retry:
            result["reflection_retry_count"] = retry_count + 1
            # Tool Call Batch Barrier：失败自省属于“非 tool feedback”，
            # 统一暂存到 deferred_feedback，由 model_node 在调用 LLM 前注入。
            # 不区分本 batch 是否已全部响应——如果 batch 中还有未响应的
            # tool_call，直接追加会把 system 消息插进 tool responses 中间；
            # 统一 deferred 也保证多条反馈的注入顺序与事件发生顺序一致。
            result["deferred_feedback"] = [
                *list(state.get("deferred_feedback") or []),
                {
                    "role": "system",
                    "content": (
                        "[失败自省]\n"
                        f"分析：{reflection.get('analysis')}\n"
                        f"建议：{reflection.get('suggestion')}\n"
                        "请仍然围绕当前计划步骤重试。"
                    ),
                },
            ]
        return result

    # --------------------------------------------------------------- finalize
    def finalize_node(state: FineGrainedCodingGraphState) -> dict[str, Any]:
        final_result = build_result_with_log(
            agent_name=agent_name,
            user_message=str(state.get("user_message") or ""),
            status=str(state.get("status") or AGENT_STATUS_FAILED),
            answer=str(state.get("answer") or ""),
            steps=list(state.get("steps") or []),
            max_steps=int(state.get("max_steps") or 8),
            error=state.get("error"),
            task_plan=state.get("task_plan"),
            executor_state=state.get("executor_state"),
        )
        approval_result = state.get("approval_result")
        if isinstance(approval_result, dict) and approval_result:
            final_result["approval_result"] = approval_result
            final_result["verification_report"] = approval_result.get(
                "verification_report"
            )
            final_result["approval_id"] = approval_result.get("approval_id")
        final_result["graph"] = {
            "workflow_version": LANGGRAPH_V2_WORKFLOW_VERSION,
            "thread_id": state.get("thread_id"),
            "orchestrator": "langgraph_v2",
        }
        return {
            "final_result": final_result,
            "graph_events": [
                build_graph_event(
                    "graph_finalized",
                    workflow_version=LANGGRAPH_V2_WORKFLOW_VERSION,
                    status=final_result.get("status"),
                )
            ],
        }

    return {
        "plan": plan_node,
        "model": model_node,
        "tool_gate": tool_gate_node,
        "executor_gate": executor_gate_node,
        "policy": policy_node,
        "execute_tool": execute_tool_node,
        "approval": approval_node,
        "reflection": reflection_node,
        "finalize": finalize_node,
    }


# ------------------------------------------------------------------ 路由函数
# 只读 state，返回下一个节点的名字。


def route_after_model(state: FineGrainedCodingGraphState) -> str:
    pending = state.get("pending_tool_calls") or []
    cursor = int(state.get("tool_cursor") or 0)
    if cursor < len(pending):
        return "tool_gate"
    if str(state.get("status") or "") in {
        AGENT_STATUS_FINISHED,
        AGENT_STATUS_FAILED,
        AGENT_STATUS_MAX_STEPS_REACHED,
        AGENT_STATUS_REJECTED,
        "policy_blocked",
        "verification_failed",
    }:
        return "finalize"
    return "model"


def route_after_tool_gate(state: FineGrainedCodingGraphState) -> str:
    exit_code = str(state.get("gate_exit") or "")
    if exit_code == "args_error":
        return "model"
    if exit_code == "tool_missing":
        return "reflection"
    return "executor_gate"


def route_after_executor_gate(state: FineGrainedCodingGraphState) -> str:
    if str(state.get("gate_exit") or "") == "blocked":
        return "model"
    return "policy"


def route_after_policy(state: FineGrainedCodingGraphState) -> str:
    exit_code = str(state.get("gate_exit") or "")
    if exit_code == "blocked":
        return "model"
    if exit_code == "require_approval":
        return "approval"
    return "execute_tool"


def route_after_execute_tool(state: FineGrainedCodingGraphState) -> str:
    if is_failed_tool_result(state.get("tool_execution") or {}):
        return "reflection"
    return "model"


def route_after_approval(state: FineGrainedCodingGraphState) -> str:
    if str(state.get("status") or "") in {
        AGENT_STATUS_REJECTED,
        AGENT_STATUS_FAILED,
        "policy_blocked",
        "verification_failed",
    }:
        return "finalize"
    return "model"
