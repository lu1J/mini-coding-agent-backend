import inspect
import json
import time
from datetime import datetime
from typing import Any

from app.agent.agent_loop import (
    build_clean_tool_calls,
    build_invalid_argument_policy_decision,
    execute_tool,
    parse_tool_arguments,
)
from app.agent.approval_store import save_pending_action
from app.agent.code_agent import (
    AVAILABLE_CODE_AGENT_TOOLS,
    CODE_AGENT_SYSTEM_PROMPT,
    CODE_AGENT_TOOLS,
)
from app.agent.execution_policy_guard import (
    POLICY_BLOCK,
    POLICY_REQUIRE_APPROVAL,
    evaluate_tool_policy,
    format_policy_feedback,
)
from app.agent.pending_execution_store import save_pending_execution_context
from app.agent.plan_execution_audit import (
    attach_plan_execution_audit,
    persist_audit_to_run_log,
)
from app.agent.plan_executor import (
    EXECUTOR_ALLOW_SUPPORTING,
    build_executor_instruction,
    can_accept_final_answer,
    create_executor_state,
    evaluate_executor_tool_call,
    get_current_executor_step,
    mark_current_step_result,
    mark_current_step_waiting_approval,
    mark_executor_max_steps,
    mark_final_answer_completed,
    mark_supporting_tool_used,
    persist_executor_state_to_run_log,
    record_executor_block,
)
from app.agent.run_logger import save_agent_run
from app.agent.status import (
    AGENT_STATUS_FAILED,
    AGENT_STATUS_FINISHED,
    AGENT_STATUS_MAX_STEPS_REACHED,
    AGENT_STATUS_WAITING_APPROVAL,
    ERROR_TYPE_MODEL,
)
from app.agent.task_planner import build_task_plan
from app.agent.tool_policy import get_tool_risk_level
from app.llm.deepseek_client import llm


CODE_AGENT_STREAM_SYSTEM_PROMPT = CODE_AGENT_SYSTEM_PROMPT


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def duration_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)


def sse_event(event: str, data: dict[str, Any]) -> str:
    payload = {"event": event, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def save_stream_log(
    *,
    status: str,
    user_message: str,
    answer: str,
    max_steps: int,
    steps: list[dict[str, Any]],
    task_plan: dict[str, Any],
    executor_state: dict[str, Any],
    plan_execution_audit: dict[str, Any],
    error: dict[str, Any] | None = None,
    pending_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "agent_name": "CodeAgentStream",
        "model_name": llm.model,
        "status": status,
        "user_message": user_message,
        "answer": answer,
        "max_steps": max_steps,
        "steps": steps,
        "error": error,
        "pending_action": pending_action,
    }
    parameters = inspect.signature(save_agent_run).parameters
    if "task_plan" in parameters:
        kwargs["task_plan"] = task_plan
    if "executor_state" in parameters:
        kwargs["executor_state"] = executor_state
    if "plan_execution_audit" in parameters:
        kwargs["plan_execution_audit"] = plan_execution_audit
    return save_agent_run(**kwargs)


def build_stream_terminal_data(
    *,
    status: str,
    user_message: str,
    answer: str,
    max_steps: int,
    steps: list[dict[str, Any]],
    task_plan: dict[str, Any],
    executor_state: dict[str, Any],
    error: dict[str, Any] | None = None,
    pending_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "answer": answer,
        "task_plan": task_plan,
        "executor_state": executor_state,
        "steps": steps,
        "error": error,
        "pending_action": pending_action,
    }
    attach_plan_execution_audit(result)
    plan_execution_audit = result["plan_execution_audit"]

    try:
        log_info = save_stream_log(
            status=status,
            user_message=user_message,
            answer=answer,
            max_steps=max_steps,
            steps=steps,
            task_plan=task_plan,
            executor_state=executor_state,
            plan_execution_audit=plan_execution_audit,
            error=error,
            pending_action=pending_action,
        )
        result.update(log_info)
        audit_error = persist_audit_to_run_log(
            log_info=log_info,
            plan_execution_audit=plan_execution_audit,
        )
        if audit_error:
            result["audit_log_error"] = audit_error

        executor_log_error = persist_executor_state_to_run_log(
            log_info=log_info,
            executor_state=executor_state,
        )
        if executor_log_error:
            result["executor_log_error"] = executor_log_error
    except Exception as error_value:
        result["log_error"] = f"日志保存失败：{str(error_value)}"

    return result


def _executor_feedback(decision: dict[str, Any]) -> str:
    return (
        "[Planner–Executor]\n"
        f"当前步骤：{decision.get('current_plan_step_index')} - "
        f"{decision.get('current_plan_step_title')}\n"
        f"期望工具：{decision.get('expected_tool')}\n"
        f"原因：{decision.get('reason')}"
    )


def run_code_agent_stream(user_message: str, max_steps: int = 8):
    """Planner–Executor v1 的 SSE 步骤流。"""
    steps: list[dict[str, Any]] = []
    task_plan = build_task_plan(user_message)
    executor_state = create_executor_state(task_plan)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": CODE_AGENT_STREAM_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    step_counter = 0
    premature_final_count = 0

    yield sse_event(
        "agent_started",
        {
            "status": "running",
            "agent_name": "CodeAgentStream",
            "model_name": llm.model,
            "message": "CodeAgentStream 开始按 Planner–Executor 执行。",
            "user_message": user_message,
            "max_steps": max_steps,
            "created_at": now_iso(),
            "task_plan": task_plan,
            "executor_state": executor_state,
        },
    )

    for model_round in range(1, max_steps + 1):
        current = get_current_executor_step(executor_state)
        yield sse_event(
            "plan_step_started",
            {
                "model_round": model_round,
                "current_plan_step": current,
                "executor_state": executor_state,
            },
        )

        model_started_at = now_iso()
        model_start_time = time.perf_counter()
        yield sse_event(
            "model_call_started",
            {
                "model_round": model_round,
                "message": "开始调用模型。",
                "started_at": model_started_at,
            },
        )

        try:
            response = llm.client.chat.completions.create(
                model=llm.model,
                messages=[
                    *messages,
                    {
                        "role": "system",
                        "content": build_executor_instruction(executor_state),
                    },
                ],
                tools=CODE_AGENT_TOOLS,
                tool_choice="auto",
            )
        except Exception as error_value:
            error = {
                "type": ERROR_TYPE_MODEL,
                "message": "模型调用失败。",
                "detail": str(error_value),
            }
            step_counter += 1
            steps.append(
                {
                    "step": step_counter,
                    "model_round": model_round,
                    "type": "model_call",
                    "content": "模型调用失败。",
                    "success": False,
                    "error": error,
                    "started_at": model_started_at,
                    "ended_at": now_iso(),
                    "duration_ms": duration_ms(model_start_time),
                }
            )
            yield sse_event(
                "agent_failed",
                build_stream_terminal_data(
                    status=AGENT_STATUS_FAILED,
                    user_message=user_message,
                    answer="模型调用失败，任务已停止。",
                    max_steps=max_steps,
                    steps=steps,
                    task_plan=task_plan,
                    executor_state=executor_state,
                    error=error,
                ),
            )
            return

        message = response.choices[0].message
        tool_calls = message.tool_calls
        yield sse_event(
            "model_call_finished",
            {
                "model_round": model_round,
                "has_tool_calls": bool(tool_calls),
                "started_at": model_started_at,
                "ended_at": now_iso(),
                "duration_ms": duration_ms(model_start_time),
            },
        )

        if not tool_calls:
            answer = message.content or ""
            if not can_accept_final_answer(executor_state):
                premature_final_count += 1
                current = get_current_executor_step(executor_state)
                feedback = (
                    "当前计划步骤尚未完成："
                    f"{current.get('plan_step_index')} - {current.get('title')}；"
                    f"请调用 {current.get('suggested_tool')}。"
                )
                step_counter += 1
                step = {
                    "step": step_counter,
                    "model_round": model_round,
                    "type": "executor_blocked",
                    "content": answer,
                    "success": False,
                    "error": {
                        "type": "premature_final_answer",
                        "message": "模型在计划完成前尝试结束。",
                        "detail": feedback,
                    },
                    "plan_step_index": current.get("plan_step_index"),
                    "plan_step_title": current.get("title"),
                    "started_at": model_started_at,
                    "ended_at": now_iso(),
                    "duration_ms": duration_ms(model_start_time),
                }
                steps.append(step)
                yield sse_event("executor_blocked", {"step": step})
                messages.extend(
                    [
                        {"role": "assistant", "content": answer},
                        {"role": "user", "content": feedback},
                    ]
                )
                if premature_final_count >= 2:
                    yield sse_event(
                        "agent_failed",
                        build_stream_terminal_data(
                            status=AGENT_STATUS_FAILED,
                            user_message=user_message,
                            answer="模型连续提前结束，执行器已停止。",
                            max_steps=max_steps,
                            steps=steps,
                            task_plan=task_plan,
                            executor_state=executor_state,
                            error={
                                "type": "executor_stalled",
                                "message": "模型未按当前步骤调用工具。",
                                "detail": feedback,
                            },
                        ),
                    )
                    return
                continue

            mark_final_answer_completed(executor_state)
            step_counter += 1
            step = {
                "step": step_counter,
                "model_round": model_round,
                "type": "final_answer",
                "content": answer,
                "success": True,
                "error": None,
                "plan_step_title": "最终总结",
                "started_at": model_started_at,
                "ended_at": now_iso(),
                "duration_ms": duration_ms(model_start_time),
            }
            steps.append(step)
            yield sse_event("final_answer", {"step": step_counter, "content": answer})
            yield sse_event(
                "agent_finished",
                build_stream_terminal_data(
                    status=AGENT_STATUS_FINISHED,
                    user_message=user_message,
                    answer=answer,
                    max_steps=max_steps,
                    steps=steps,
                    task_plan=task_plan,
                    executor_state=executor_state,
                ),
            )
            return

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": build_clean_tool_calls(tool_calls),
            }
        )

        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            risk_level = get_tool_risk_level(tool_name)
            current_before = get_current_executor_step(executor_state)
            tool_args, argument_error = parse_tool_arguments(tool_call)
            executor_decision = evaluate_executor_tool_call(
                executor_state=executor_state,
                tool_name=tool_name,
                risk_level=risk_level,
                tool_args=tool_args or {},
            )
            yield sse_event(
                "executor_checked",
                {
                    "model_round": model_round,
                    "tool_name": tool_name,
                    "executor_decision": executor_decision,
                },
            )

            if not executor_decision["allowed"]:
                feedback = _executor_feedback(executor_decision)
                record_executor_block(executor_state, executor_decision)
                step_counter += 1
                step = {
                    "step": step_counter,
                    "model_round": model_round,
                    "type": "executor_blocked",
                    "tool_name": tool_name,
                    "tool_args": {},
                    "tool_result": feedback,
                    "risk_level": risk_level,
                    "success": False,
                    "error": {
                        "type": "plan_step_violation",
                        "message": "工具调用不符合当前计划步骤。",
                        "detail": feedback,
                    },
                    "executor_decision": executor_decision,
                    "plan_step_index": executor_decision.get("current_plan_step_index"),
                    "plan_step_title": executor_decision.get("current_plan_step_title"),
                    "started_at": now_iso(),
                    "ended_at": now_iso(),
                    "duration_ms": 0,
                }
                steps.append(step)
                yield sse_event("executor_blocked", {"step": step})
                messages.append(
                    {"role": "tool", "tool_call_id": tool_call.id, "content": feedback}
                )
                continue

            if argument_error:
                policy_decision = build_invalid_argument_policy_decision(
                    tool_name=tool_name,
                    risk_level=risk_level,
                    error=argument_error,
                )
                feedback = format_policy_feedback(policy_decision)
                step_counter += 1
                step = {
                    "step": step_counter,
                    "model_round": model_round,
                    "type": "policy_blocked",
                    "tool_name": tool_name,
                    "tool_args": {},
                    "risk_level": risk_level,
                    "tool_result": feedback,
                    "success": False,
                    "error": argument_error,
                    "policy_decision": policy_decision,
                    "executor_decision": executor_decision,
                    "plan_step_index": executor_decision.get("current_plan_step_index"),
                    "plan_step_title": executor_decision.get("current_plan_step_title"),
                    "started_at": now_iso(),
                    "ended_at": now_iso(),
                    "duration_ms": 0,
                }
                steps.append(step)
                yield sse_event("policy_blocked", {"step": step})
                messages.append(
                    {"role": "tool", "tool_call_id": tool_call.id, "content": feedback}
                )
                continue

            policy_decision = evaluate_tool_policy(
                task_plan=task_plan,
                tool_name=tool_name,
                tool_args=tool_args,
                risk_level=risk_level,
            )
            yield sse_event(
                "policy_checked",
                {
                    "model_round": model_round,
                    "tool_name": tool_name,
                    "policy_decision": policy_decision,
                },
            )

            if policy_decision["decision"] == POLICY_BLOCK:
                feedback = format_policy_feedback(policy_decision)
                step_counter += 1
                step = {
                    "step": step_counter,
                    "model_round": model_round,
                    "type": "policy_blocked",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "risk_level": risk_level,
                    "tool_result": feedback,
                    "success": False,
                    "error": {
                        "type": "policy_violation",
                        "message": "Execution Policy Guard 已拦截。",
                        "detail": feedback,
                    },
                    "policy_decision": policy_decision,
                    "executor_decision": executor_decision,
                    "plan_step_index": executor_decision.get("current_plan_step_index"),
                    "plan_step_title": executor_decision.get("current_plan_step_title"),
                    "started_at": now_iso(),
                    "ended_at": now_iso(),
                    "duration_ms": 0,
                }
                steps.append(step)
                yield sse_event("policy_blocked", {"step": step})
                messages.append(
                    {"role": "tool", "tool_call_id": tool_call.id, "content": feedback}
                )
                continue

            if policy_decision["decision"] == POLICY_REQUIRE_APPROVAL:
                mark_current_step_waiting_approval(executor_state, tool_name=tool_name)
                pending_action = save_pending_action(
                    agent_name="CodeAgentStream",
                    user_message=user_message,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    risk_level=risk_level,
                    reason=(
                        "Planner–Executor 已确认当前步骤；"
                        "Execution Policy Guard 已确认目标范围；仍需用户批准。"
                    ),
                )
                step_counter += 1
                step = {
                    "step": step_counter,
                    "model_round": model_round,
                    "type": "approval_required",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "risk_level": risk_level,
                    "tool_result": "当前计划步骤已暂停，等待用户审批。",
                    "content": "审批后将从原计划下一步骤继续。",
                    "success": None,
                    "error": None,
                    "policy_decision": policy_decision,
                    "executor_decision": executor_decision,
                    "plan_step_index": executor_decision.get("current_plan_step_index"),
                    "plan_step_title": executor_decision.get("current_plan_step_title"),
                    "started_at": now_iso(),
                    "ended_at": now_iso(),
                    "duration_ms": 0,
                }
                steps.append(step)
                approval_id = str(pending_action.get("approval_id") or "")
                if approval_id:
                    pending_action["executor_context_path"] = save_pending_execution_context(
                        approval_id=approval_id,
                        task_plan=task_plan,
                        executor_state=executor_state,
                        max_steps=max_steps,
                        steps=steps,
                    )
                terminal = build_stream_terminal_data(
                    status=AGENT_STATUS_WAITING_APPROVAL,
                    user_message=user_message,
                    answer=f"计划步骤 {executor_decision.get('current_plan_step_index')} 等待审批。",
                    max_steps=max_steps,
                    steps=steps,
                    task_plan=task_plan,
                    executor_state=executor_state,
                    pending_action=pending_action,
                )
                terminal["step"] = step
                yield sse_event("approval_required", terminal)
                return

            tool_started_at = now_iso()
            tool_start_time = time.perf_counter()
            yield sse_event(
                "tool_call_started",
                {
                    "model_round": model_round,
                    "tool_name": tool_name,
                    "plan_step_index": executor_decision.get("current_plan_step_index"),
                    "started_at": tool_started_at,
                },
            )
            execution = execute_tool(
                tool_call=tool_call,
                available_tools=AVAILABLE_CODE_AGENT_TOOLS,
                agent_name="CodeAgentStream",
                parsed_tool_args=tool_args,
            )

            if executor_decision["decision"] == EXECUTOR_ALLOW_SUPPORTING:
                mark_supporting_tool_used(executor_state, tool_name=tool_name)
            elif executor_decision.get("completes_current_step"):
                mark_current_step_result(
                    executor_state,
                    tool_name=tool_name,
                    success=bool(execution["success"]),
                    error=execution.get("error"),
                    tool_args=tool_args,
                )

            step_counter += 1
            step = {
                "step": step_counter,
                "model_round": model_round,
                "type": "tool_call",
                "tool_name": tool_name,
                "tool_args": execution["tool_args"],
                "risk_level": risk_level,
                "tool_result": execution["tool_result"],
                "success": execution["success"],
                "error": execution["error"],
                "policy_decision": policy_decision,
                "executor_decision": executor_decision,
                "plan_step_index": (
                    current_before.get("plan_step_index") if current_before else None
                ),
                "plan_step_title": current_before.get("title") if current_before else None,
                "started_at": tool_started_at,
                "ended_at": now_iso(),
                "duration_ms": duration_ms(tool_start_time),
            }
            steps.append(step)
            yield sse_event(
                "tool_call_finished",
                {"step": step, "executor_state": executor_state},
            )
            if executor_decision.get("completes_current_step") and execution["success"]:
                yield sse_event(
                    "plan_step_completed",
                    {
                        "plan_step_index": step.get("plan_step_index"),
                        "plan_step_title": step.get("plan_step_title"),
                        "executor_state": executor_state,
                    },
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": execution["tool_call_id"],
                    "content": execution["tool_result"],
                }
            )

    mark_executor_max_steps(executor_state)
    yield sse_event(
        "max_steps_reached",
        build_stream_terminal_data(
            status=AGENT_STATUS_MAX_STEPS_REACHED,
            user_message=user_message,
            answer="CodeAgentStream 达到最大模型轮次，已停止。",
            max_steps=max_steps,
            steps=steps,
            task_plan=task_plan,
            executor_state=executor_state,
        ),
    )
