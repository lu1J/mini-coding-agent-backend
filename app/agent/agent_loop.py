import inspect
import json
import time
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable

from app.agent.approval_store import save_pending_action
from app.agent.execution_policy_guard import (
    POLICY_BLOCK,
    POLICY_GUARD_VERSION,
    POLICY_REQUIRE_APPROVAL,
    evaluate_tool_policy,
    format_policy_feedback,
)
from app.agent.pending_execution_store import (
    save_pending_execution_context,
)
from app.agent.plan_execution_audit import (
    attach_plan_execution_audit,
    persist_audit_to_run_log,
)
from app.agent.plan_executor import (
    EXECUTOR_ALLOW_SUPPORTING,
    build_executor_instruction,
    can_accept_degraded_final_answer,
    can_accept_final_answer,
    clone_executor_state,
    create_executor_state,
    evaluate_executor_tool_call,
    get_current_executor_step,
    mark_current_step_result,
    mark_current_step_waiting_approval,
    mark_degraded_final_answer_completed,
    mark_executor_max_steps,
    mark_final_answer_completed,
    mark_supporting_tool_used,
    persist_executor_state_to_run_log,
    record_executor_block,
)
from app.agent.reflection import (
    build_max_steps_reflection,
    build_tool_error_reflection,
    should_retry_from_reflection,
)
from app.agent.run_logger import save_agent_run
from app.agent.status import (
    AGENT_STATUS_FAILED,
    AGENT_STATUS_FINISHED,
    AGENT_STATUS_MAX_STEPS_REACHED,
    AGENT_STATUS_WAITING_APPROVAL,
    ERROR_TYPE_MODEL,
    ERROR_TYPE_TOOL,
)
from app.agent.task_planner import build_task_plan
from app.agent.tool_policy import get_tool_risk_level
from app.llm.deepseek_client import llm


def is_failed_tool_result(tool_result: dict[str, Any] | None) -> bool:
    if not isinstance(tool_result, dict):
        return False
    return tool_result.get("success") is False or bool(tool_result.get("error"))


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def duration_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)


def infer_error_from_legacy_tool_result(
    result_text: str,
) -> dict[str, Any] | None:
    text = str(result_text or "").strip()
    lower_text = text.lower()

    if not text:
        return None

    if (
        "文件不存在" in text
        or "路径不存在" in text
        or "不存在：" in text
        or "not found" in lower_text
        or "no such file" in lower_text
    ):
        return {
            "type": "file_not_found",
            "message": "工具返回文件或路径不存在。",
            "detail": text,
        }

    if (
        "权限" in text
        or "permission" in lower_text
        or "denied" in lower_text
    ):
        return {
            "type": "permission_denied",
            "message": "工具返回权限或安全策略错误。",
            "detail": text,
        }

    if (
        "syntaxerror" in lower_text
        or "traceback" in lower_text
        or "退出码" in text
        or "command failed" in lower_text
    ):
        return {
            "type": "command_failed",
            "message": "工具返回命令执行失败。",
            "detail": text,
        }

    if text.startswith("错误：") or text.startswith("工具执行失败"):
        return {
            "type": "tool_error",
            "message": "工具返回错误信息。",
            "detail": text,
        }

    return None


def build_clean_tool_calls(tool_calls) -> list[dict[str, Any]]:
    clean_tool_calls = []
    for tool_call in tool_calls:
        clean_tool_calls.append(
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
        )
    return clean_tool_calls


def parse_tool_arguments(
    tool_call,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    raw_arguments = tool_call.function.arguments or "{}"
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as error_value:
        return None, {
            "type": ERROR_TYPE_TOOL,
            "message": "工具参数解析失败。",
            "detail": str(error_value),
        }

    if not isinstance(parsed, dict):
        return None, {
            "type": ERROR_TYPE_TOOL,
            "message": "工具参数必须是 JSON 对象。",
            "detail": f"实际类型：{type(parsed).__name__}",
        }

    return parsed, None


def build_invalid_argument_policy_decision(
    *,
    tool_name: str,
    risk_level: str,
    error: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": POLICY_GUARD_VERSION,
        "decision": POLICY_BLOCK,
        "allowed": False,
        "requires_approval": False,
        "tool_name": tool_name,
        "risk_level": risk_level,
        "planned": False,
        "planned_tools": [],
        "planned_target_paths": [],
        "requested_paths": [],
        "matched_paths": [],
        "outside_paths": [],
        "command": "",
        "violations": [
            {
                "code": "invalid_tool_arguments",
                "message": error.get("message", "工具参数无效。"),
            }
        ],
        "reasons": [
            "模型返回的工具参数无法安全解析，按 Fail Closed 拦截。"
        ],
    }


def execute_tool(
    tool_call,
    available_tools: dict[str, Callable[..., Any]],
    agent_name: str = "Agent",
    parsed_tool_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool_name = tool_call.function.name
    tool_args = parsed_tool_args

    if tool_args is None:
        tool_args, argument_error = parse_tool_arguments(tool_call)
        if argument_error:
            return {
                "tool_call_id": tool_call.id,
                "tool_name": tool_name,
                "tool_args": {},
                "tool_result": "工具参数不是合法 JSON，未执行工具。",
                "success": False,
                "error": argument_error,
            }

    tool_func = available_tools.get(tool_name)
    if not tool_func:
        return {
            "tool_call_id": tool_call.id,
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

    try:
        raw_result = tool_func(**tool_args)

        if isinstance(raw_result, dict) and "success" in raw_result:
            return {
                "tool_call_id": tool_call.id,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_result": str(raw_result.get("result", "")),
                "success": bool(raw_result.get("success")),
                "error": raw_result.get("error"),
            }

        tool_result_text = str(raw_result)
        legacy_error = infer_error_from_legacy_tool_result(tool_result_text)
        return {
            "tool_call_id": tool_call.id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_result": tool_result_text,
            "success": legacy_error is None,
            "error": legacy_error,
        }
    except Exception as error_value:
        return {
            "tool_call_id": tool_call.id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_result": f"工具执行失败：{str(error_value)}",
            "success": False,
            "error": {
                "type": ERROR_TYPE_TOOL,
                "message": "工具执行失败。",
                "detail": str(error_value),
            },
        }


def _save_agent_run_compatible(
    *,
    agent_name: str,
    user_message: str,
    status: str,
    answer: str,
    steps: list[dict[str, Any]],
    max_steps: int,
    error: dict[str, Any] | None,
    pending_action: dict[str, Any] | None,
    task_plan: dict[str, Any] | None,
    plan_execution_audit: dict[str, Any],
    executor_state: dict[str, Any] | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "agent_name": agent_name,
        "user_message": user_message,
        "status": status,
        "answer": answer,
        "steps": steps,
        "max_steps": max_steps,
        "model_name": llm.model,
        "error": error,
        "pending_action": pending_action,
    }

    parameters = inspect.signature(save_agent_run).parameters
    if "task_plan" in parameters:
        kwargs["task_plan"] = task_plan
    if "plan_execution_audit" in parameters:
        kwargs["plan_execution_audit"] = plan_execution_audit
    if "executor_state" in parameters:
        kwargs["executor_state"] = executor_state

    return save_agent_run(**kwargs)


def build_result_with_log(
    *,
    agent_name: str,
    user_message: str,
    status: str,
    answer: str,
    steps: list[dict[str, Any]],
    max_steps: int,
    error: dict[str, Any] | None = None,
    pending_action: dict[str, Any] | None = None,
    task_plan: dict[str, Any] | None = None,
    executor_state: dict[str, Any] | None = None,
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

    try:
        log_info = _save_agent_run_compatible(
            agent_name=agent_name,
            user_message=user_message,
            status=status,
            answer=answer,
            steps=steps,
            max_steps=max_steps,
            error=error,
            pending_action=pending_action,
            task_plan=task_plan,
            plan_execution_audit=result["plan_execution_audit"],
            executor_state=executor_state,
        )
        result.update(log_info)

        audit_log_error = persist_audit_to_run_log(
            log_info=log_info,
            plan_execution_audit=result["plan_execution_audit"],
        )
        if audit_log_error:
            result["audit_log_error"] = audit_log_error

        executor_log_error = persist_executor_state_to_run_log(
            log_info=log_info,
            executor_state=executor_state or {},
        )
        if executor_log_error:
            result["executor_log_error"] = executor_log_error
    except Exception as error_value:
        result["log_error"] = f"日志保存失败：{str(error_value)}"

    return result


def _executor_feedback(decision: dict[str, Any]) -> str:
    return (
        "[Planner–Executor]\n"
        f"决策：{decision.get('decision')}\n"
        f"当前计划步骤：{decision.get('current_plan_step_index')} - "
        f"{decision.get('current_plan_step_title')}\n"
        f"期望工具：{decision.get('expected_tool')}\n"
        f"原因：{decision.get('reason')}\n"
        "请严格执行当前步骤，不要提前或重复调用其他工具。"
    )


def run_agent_loop(
    *,
    agent_name: str,
    system_prompt: str,
    tools: list[dict[str, Any]],
    available_tools: dict[str, Callable[..., Any]],
    user_message: str,
    max_steps: int = 8,
    task_plan_override: dict[str, Any] | None = None,
    executor_state_override: dict[str, Any] | None = None,
    resume_context: str | None = None,
    initial_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Planner–Executor v1：按照计划步骤受控执行。"""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    if resume_context:
        messages.append(
            {
                "role": "system",
                "content": "[审批后恢复上下文]\n" + resume_context,
            }
        )

    steps = list(initial_steps or [])
    task_plan = deepcopy(task_plan_override) if task_plan_override else build_task_plan(user_message)
    executor_state = (
        clone_executor_state(executor_state_override)
        if executor_state_override
        else create_executor_state(task_plan)
    )

    step_counter = max(
        [int(step.get("step") or 0) for step in steps if isinstance(step, dict)],
        default=0,
    )
    reflection_retry_count = 0
    max_reflection_retries = 1
    premature_final_count = 0

    for model_round in range(1, max_steps + 1):
        model_started_at = now_iso()
        model_start_time = time.perf_counter()
        request_messages = [
            *messages,
            {
                "role": "system",
                "content": build_executor_instruction(executor_state),
            },
        ]

        try:
            response = llm.client.chat.completions.create(
                model=llm.model,
                messages=request_messages,
                tools=tools,
                tool_choice="auto",
            )
        except Exception as error_value:
            error = {
                "type": ERROR_TYPE_MODEL,
                "message": "模型调用失败，任务已停止。",
                "detail": str(error_value),
            }
            step_counter += 1
            steps.append(
                {
                    "step": step_counter,
                    "model_round": model_round,
                    "type": "model_call",
                    "success": False,
                    "error": error,
                    "started_at": model_started_at,
                    "ended_at": now_iso(),
                    "duration_ms": duration_ms(model_start_time),
                    "content": "模型调用失败。",
                }
            )
            return build_result_with_log(
                agent_name=agent_name,
                user_message=user_message,
                status=AGENT_STATUS_FAILED,
                answer="模型调用失败，任务已停止。",
                steps=steps,
                max_steps=max_steps,
                error=error,
                task_plan=task_plan,
                executor_state=executor_state,
            )

        assistant_message = response.choices[0].message
        model_ended_at = now_iso()
        model_duration = duration_ms(model_start_time)

        if not assistant_message.tool_calls:
            final_answer = assistant_message.content or ""

            if not can_accept_final_answer(executor_state):
                if can_accept_degraded_final_answer(executor_state):
                    current = get_current_executor_step(executor_state)
                    mark_degraded_final_answer_completed(executor_state)
                    step_counter += 1
                    steps.append(
                        {
                            "step": step_counter,
                            "model_round": model_round,
                            "type": "final_answer",
                            "content": final_answer,
                            "success": True,
                            "error": None,
                            "degraded": True,
                            "plan_step_index": (
                                current.get("plan_step_index") if current else None
                            ),
                            "plan_step_title": (
                                current.get("title") if current else None
                            ),
                            "started_at": model_started_at,
                            "ended_at": model_ended_at,
                            "duration_ms": model_duration,
                        }
                    )
                    return build_result_with_log(
                        agent_name=agent_name,
                        user_message=user_message,
                        status=AGENT_STATUS_FINISHED,
                        answer=final_answer,
                        steps=steps,
                        max_steps=max_steps,
                        task_plan=task_plan,
                        executor_state=executor_state,
                    )

                premature_final_count += 1
                current = get_current_executor_step(executor_state)
                feedback = (
                    "[Planner–Executor]\n"
                    "当前计划步骤尚未完成，暂时不能结束任务。\n"
                    f"当前步骤：{current.get('plan_step_index')} - {current.get('title')}\n"
                    f"请调用：{current.get('suggested_tool')}"
                )
                step_counter += 1
                steps.append(
                    {
                        "step": step_counter,
                        "model_round": model_round,
                        "type": "executor_blocked",
                        "content": final_answer,
                        "success": False,
                        "error": {
                            "type": "premature_final_answer",
                            "message": "模型在计划完成前尝试结束任务。",
                            "detail": feedback,
                        },
                        "plan_step_index": current.get("plan_step_index"),
                        "plan_step_title": current.get("title"),
                        "started_at": model_started_at,
                        "ended_at": model_ended_at,
                        "duration_ms": model_duration,
                    }
                )
                messages.append(
                    {"role": "assistant", "content": final_answer}
                )
                messages.append({"role": "user", "content": feedback})

                if premature_final_count >= 2:
                    return build_result_with_log(
                        agent_name=agent_name,
                        user_message=user_message,
                        status=AGENT_STATUS_FAILED,
                        answer="模型连续两次提前结束，Planner–Executor 已停止任务。",
                        steps=steps,
                        max_steps=max_steps,
                        error={
                            "type": "executor_stalled",
                            "message": "模型未按当前计划步骤调用工具。",
                            "detail": feedback,
                        },
                        task_plan=task_plan,
                        executor_state=executor_state,
                    )
                continue

            mark_final_answer_completed(executor_state)
            step_counter += 1
            current = get_current_executor_step(executor_state)
            steps.append(
                {
                    "step": step_counter,
                    "model_round": model_round,
                    "type": "final_answer",
                    "content": final_answer,
                    "success": True,
                    "error": None,
                    "plan_step_index": (
                        current.get("plan_step_index") if current else None
                    ),
                    "plan_step_title": (
                        current.get("title") if current else "最终总结"
                    ),
                    "started_at": model_started_at,
                    "ended_at": model_ended_at,
                    "duration_ms": model_duration,
                }
            )
            return build_result_with_log(
                agent_name=agent_name,
                user_message=user_message,
                status=AGENT_STATUS_FINISHED,
                answer=final_answer,
                steps=steps,
                max_steps=max_steps,
                task_plan=task_plan,
                executor_state=executor_state,
            )

        clean_tool_calls = build_clean_tool_calls(assistant_message.tool_calls)
        messages.append(
            {
                "role": "assistant",
                "content": assistant_message.content or "",
                "tool_calls": clean_tool_calls,
            }
        )

        for tool_call in assistant_message.tool_calls:
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

            if not executor_decision["allowed"]:
                feedback = _executor_feedback(executor_decision)
                record_executor_block(executor_state, executor_decision)
                step_counter += 1
                steps.append(
                    {
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
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": feedback,
                    }
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
                steps.append(
                    {
                        "step": step_counter,
                        "model_round": model_round,
                        "type": "policy_blocked",
                        "tool_name": tool_name,
                        "tool_args": {},
                        "risk_level": risk_level,
                        "tool_result": feedback,
                        "content": "工具参数无效，执行前策略已拦截。",
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
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": feedback,
                    }
                )
                continue

            policy_decision = evaluate_tool_policy(
                task_plan=task_plan,
                tool_name=tool_name,
                tool_args=tool_args,
                risk_level=risk_level,
            )

            if policy_decision["decision"] == POLICY_BLOCK:
                feedback = format_policy_feedback(policy_decision)
                error = {
                    "type": "policy_violation",
                    "message": "工具调用被 Execution Policy Guard 拦截。",
                    "detail": feedback,
                }
                step_counter += 1
                steps.append(
                    {
                        "step": step_counter,
                        "model_round": model_round,
                        "type": "policy_blocked",
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "risk_level": risk_level,
                        "tool_result": feedback,
                        "content": "工具未执行，模型可以根据策略反馈调整参数。",
                        "success": False,
                        "error": error,
                        "policy_decision": policy_decision,
                        "executor_decision": executor_decision,
                        "plan_step_index": executor_decision.get("current_plan_step_index"),
                        "plan_step_title": executor_decision.get("current_plan_step_title"),
                        "started_at": now_iso(),
                        "ended_at": now_iso(),
                        "duration_ms": 0,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": feedback,
                    }
                )
                continue

            if policy_decision["decision"] == POLICY_REQUIRE_APPROVAL:
                mark_current_step_waiting_approval(
                    executor_state,
                    tool_name=tool_name,
                )
                pending_action = save_pending_action(
                    agent_name=agent_name,
                    user_message=user_message,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    risk_level=risk_level,
                    reason=(
                        "Planner–Executor 已确认当前步骤；"
                        "Execution Policy Guard 已确认目标范围合法；"
                        f"工具 {tool_name} 仍需用户批准。"
                    ),
                )
                step_counter += 1
                approval_step = {
                    "step": step_counter,
                    "model_round": model_round,
                    "type": "approval_required",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "risk_level": risk_level,
                    "tool_result": "当前计划步骤与目标范围检查通过，高风险工具尚未执行。",
                    "content": "等待用户确认后继续当前计划。",
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
                steps.append(approval_step)

                approval_id = str(pending_action.get("approval_id") or "")
                if approval_id:
                    context_path = save_pending_execution_context(
                        approval_id=approval_id,
                        task_plan=task_plan,
                        executor_state=executor_state,
                        max_steps=max_steps,
                        steps=steps,
                    )
                    pending_action["executor_context_path"] = context_path
                return build_result_with_log(
                    agent_name=agent_name,
                    user_message=user_message,
                    status=AGENT_STATUS_WAITING_APPROVAL,
                    answer=(
                        f"{agent_name} 已执行到计划步骤 "
                        f"{executor_decision.get('current_plan_step_index')}，"
                        f"准备调用 {tool_name}，需要用户批准后从该计划继续。"
                    ),
                    steps=steps,
                    max_steps=max_steps,
                    pending_action=pending_action,
                    task_plan=task_plan,
                    executor_state=executor_state,
                )

            tool_started_at = now_iso()
            tool_start_time = time.perf_counter()
            tool_execution = execute_tool(
                tool_call=tool_call,
                available_tools=available_tools,
                agent_name=agent_name,
                parsed_tool_args=tool_args,
            )
            tool_ended_at = now_iso()

            if executor_decision["decision"] == EXECUTOR_ALLOW_SUPPORTING:
                mark_supporting_tool_used(
                    executor_state,
                    tool_name=tool_name,
                )
            elif executor_decision.get("completes_current_step"):
                mark_current_step_result(
                    executor_state,
                    tool_name=tool_name,
                    success=bool(tool_execution["success"]),
                    error=tool_execution.get("error"),
                    tool_args=tool_args,
                )

            reflection = None
            retry_from_reflection = False
            tool_message_content = tool_execution["tool_result"]

            if is_failed_tool_result(tool_execution):
                reflection = build_tool_error_reflection(
                    tool_name=tool_name,
                    tool_args=tool_execution["tool_args"],
                    tool_result=tool_execution,
                    error=tool_execution.get("error"),
                )
                if should_retry_from_reflection(
                    reflection=reflection,
                    retry_count=reflection_retry_count,
                    max_retries=max_reflection_retries,
                ):
                    retry_from_reflection = True
                    reflection_retry_count += 1
                    tool_message_content = (
                        f"{tool_execution['tool_result']}\n\n"
                        "[失败自省]\n"
                        f"分析：{reflection.get('analysis')}\n"
                        f"建议：{reflection.get('suggestion')}\n"
                        "请仍然围绕当前计划步骤重试。"
                    )

            step_counter += 1
            steps.append(
                {
                    "step": step_counter,
                    "model_round": model_round,
                    "type": "tool_call",
                    "tool_name": tool_name,
                    "tool_args": tool_execution["tool_args"],
                    "risk_level": risk_level,
                    "tool_result": tool_execution["tool_result"],
                    "success": tool_execution["success"],
                    "error": tool_execution["error"],
                    "reflection": reflection,
                    "retry_from_reflection": retry_from_reflection,
                    "policy_decision": policy_decision,
                    "executor_decision": executor_decision,
                    "plan_step_index": (
                        current_before.get("plan_step_index")
                        if current_before else None
                    ),
                    "plan_step_title": (
                        current_before.get("title") if current_before else None
                    ),
                    "started_at": tool_started_at,
                    "ended_at": tool_ended_at,
                    "duration_ms": duration_ms(tool_start_time),
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_execution["tool_call_id"],
                    "content": tool_message_content,
                }
            )

    mark_executor_max_steps(executor_state)
    answer = f"{agent_name} 达到最大模型轮次，Planner–Executor 已停止。"
    reflection = build_max_steps_reflection(
        max_steps=max_steps,
        completed_steps=len(steps),
        user_message=user_message,
    )
    step_counter += 1
    steps.append(
        {
            "step": step_counter,
            "model_round": max_steps,
            "type": "reflection",
            "content": "达到最大模型轮次，生成失败自省结果。",
            "success": False,
            "error": {
                "type": "max_steps_reached",
                "message": "达到最大模型轮次。",
                "detail": None,
            },
            "reflection": reflection,
            "started_at": now_iso(),
            "ended_at": now_iso(),
            "duration_ms": 0,
        }
    )
    return build_result_with_log(
        agent_name=agent_name,
        user_message=user_message,
        status=AGENT_STATUS_MAX_STEPS_REACHED,
        answer=answer,
        steps=steps,
        max_steps=max_steps,
        task_plan=task_plan,
        executor_state=executor_state,
    )
