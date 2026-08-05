from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from app.agent.approval_store import (
    delete_pending_action,
    read_pending_action,
)
from app.agent.change_verifier import execute_verified_change
from app.agent.execution_policy_guard import (
    POLICY_BLOCK,
    evaluate_tool_policy,
    format_policy_feedback,
)
from app.agent.pending_execution_store import (
    delete_pending_execution_context,
    read_pending_execution_context,
)
from app.agent.plan_executor import (
    create_executor_state,
    mark_approved_step_completed,
)
from app.agent.task_planner import build_task_plan


VERIFIED_APPROVAL_VERSION = "2.0"


def _load_code_agent_runtime():
    """延迟加载 LLM、CodeAgent 配置与 Planner–Executor Loop。"""
    from app.agent.agent_loop import run_agent_loop
    from app.agent.code_agent import (
        AVAILABLE_CODE_AGENT_TOOLS,
        CODE_AGENT_SYSTEM_PROMPT,
        CODE_AGENT_TOOLS,
    )

    return {
        "available_tools": AVAILABLE_CODE_AGENT_TOOLS,
        "system_prompt": CODE_AGENT_SYSTEM_PROMPT,
        "tools": CODE_AGENT_TOOLS,
        "run_agent_loop": run_agent_loop,
    }


def _normalize_code_agent_runtime(
    runtime: Any,
) -> dict[str, Any]:
    """
    兼容 Day 13 的二元组运行时和 Day 14 的字典运行时。

    Day 13：
        (available_tools, agent_runner)

    Day 14：
        {
            "available_tools": ...,
            "system_prompt": ...,
            "tools": ...,
            "run_agent_loop": ...,
        }
    """
    if isinstance(runtime, dict):
        return runtime

    if isinstance(runtime, tuple) and len(runtime) == 2:
        available_tools, agent_runner = runtime
        return {
            "available_tools": available_tools,
            "system_prompt": "",
            "tools": [],
            "run_agent_loop": agent_runner,
        }

    raise TypeError(
        "_load_code_agent_runtime() 必须返回运行时字典，"
        "或兼容的 (available_tools, agent_runner) 二元组。"
    )


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _build_approved_tool_step(
    *,
    existing_steps: list[dict[str, Any]],
    tool_name: str,
    tool_args: dict[str, Any],
    risk_level: str,
    tool_execution: dict[str, Any],
    verification_report: dict[str, Any],
    policy_decision: dict[str, Any],
) -> dict[str, Any]:
    """把审批接口中的真实写操作补进完整执行轨迹。"""
    approval_step = next(
        (
            step
            for step in reversed(existing_steps)
            if isinstance(step, dict)
            and step.get("type") == "approval_required"
            and step.get("tool_name") == tool_name
        ),
        {},
    )
    next_step_number = max(
        (
            int(step.get("step") or 0)
            for step in existing_steps
            if isinstance(step, dict)
        ),
        default=0,
    ) + 1

    approved_policy_decision = deepcopy(policy_decision)
    approved_policy_decision["decision"] = "allow"
    approved_policy_decision["allowed"] = True
    approved_policy_decision["requires_approval"] = False
    approved_policy_decision["approved_execution"] = True
    approved_policy_decision.setdefault("reasons", []).append(
        "用户已经批准该高风险步骤，审批接口完成二次策略检查后执行。"
    )

    return {
        "step": next_step_number,
        "model_round": approval_step.get("model_round"),
        "type": "tool_call",
        "source": "approval_execution",
        "tool_name": tool_name,
        "tool_args": deepcopy(tool_args),
        "tool_result": str(tool_execution.get("result", "")),
        "risk_level": risk_level,
        "success": bool(tool_execution.get("success")),
        "error": tool_execution.get("error"),
        "reflection": None,
        "retry_from_reflection": False,
        "policy_decision": approved_policy_decision,
        "approval_policy_decision": deepcopy(policy_decision),
        "executor_decision": deepcopy(approval_step.get("executor_decision")),
        "plan_step_index": approval_step.get("plan_step_index"),
        "plan_step_title": approval_step.get("plan_step_title"),
        "verification_status": verification_report.get("status"),
        "started_at": _now_iso(),
        "ended_at": _now_iso(),
        "duration_ms": int(verification_report.get("duration_ms") or 0),
    }


def _base_response(
    *,
    status: str,
    message: str,
    approval_id: str,
    pending_action: dict[str, Any] | None,
) -> dict[str, Any]:
    pending_action = pending_action or {}
    return {
        "status": status,
        "message": message,
        "approval_id": approval_id,
        "tool_name": pending_action.get("tool_name"),
        "tool_args": pending_action.get("tool_args"),
        "risk_level": pending_action.get("risk_level", "high"),
        "tool_result": None,
        "success": False,
        "error": None,
        "verification_report": None,
        "resume_result": None,
    }


def _should_run_tests(task_plan: dict[str, Any]) -> bool:
    intents = task_plan.get("intents", [])
    return isinstance(intents, list) and "test" in intents


def _remaining_model_rounds(
    executor_state: dict[str, Any],
    fallback: int,
) -> int:
    steps = executor_state.get("steps") or []
    position = int(executor_state.get("current_step_position") or 0)
    remaining = max(0, len(steps) - position)
    return max(3, min(max(fallback, 3), remaining + 3))


def execute_verified_approval(
    *,
    approval_id: str,
    approved: bool,
) -> dict[str, Any]:
    """审批后验证写入，并从保存的计划步骤继续执行。"""
    pending_action = read_pending_action(approval_id)

    if not pending_action:
        return _base_response(
            status="not_found",
            message="待确认动作不存在或已经处理。",
            approval_id=approval_id,
            pending_action=None,
        )

    response = _base_response(
        status="pending",
        message="",
        approval_id=approval_id,
        pending_action=pending_action,
    )

    if not approved:
        delete_pending_action(approval_id)
        delete_pending_execution_context(approval_id)
        response.update(
            {
                "status": "rejected",
                "message": "用户拒绝执行该动作，待确认动作和计划状态已删除。",
                "success": False,
            }
        )
        return response

    tool_name = str(pending_action.get("tool_name") or "")
    tool_args = pending_action.get("tool_args") or {}
    risk_level = str(pending_action.get("risk_level") or "high")
    original_user_message = str(pending_action.get("user_message") or "")

    saved_context = read_pending_execution_context(approval_id) or {}
    task_plan = saved_context.get("task_plan")
    if not isinstance(task_plan, dict):
        task_plan = build_task_plan(original_user_message)

    executor_state = saved_context.get("executor_state")
    if not isinstance(executor_state, dict):
        executor_state = create_executor_state(task_plan)

    saved_steps = saved_context.get("steps")
    if not isinstance(saved_steps, list):
        saved_steps = []
    pre_approval_steps = [
        deepcopy(step)
        for step in saved_steps
        if isinstance(step, dict)
    ]

    # 审批时再次检查，防止旧 Pending Action 绕过最新策略。
    policy_decision = evaluate_tool_policy(
        task_plan=task_plan,
        tool_name=tool_name,
        tool_args=tool_args,
        risk_level=risk_level,
    )

    if policy_decision["decision"] == POLICY_BLOCK:
        delete_pending_action(approval_id)
        delete_pending_execution_context(approval_id)
        feedback = format_policy_feedback(policy_decision)
        response.update(
            {
                "status": "policy_blocked",
                "message": "审批执行前的二次策略检查未通过。",
                "success": False,
                "error": {
                    "type": "policy_violation",
                    "message": "待确认动作已不符合当前执行策略。",
                    "detail": feedback,
                },
                "verification_report": {
                    "version": VERIFIED_APPROVAL_VERSION,
                    "status": "not_executed",
                    "policy_decision": policy_decision,
                    "executor_state": executor_state,
                },
            }
        )
        return response

    runtime = _normalize_code_agent_runtime(
        _load_code_agent_runtime()
    )
    tool_func = runtime["available_tools"].get(tool_name)

    if not tool_func:
        delete_pending_action(approval_id)
        delete_pending_execution_context(approval_id)
        response.update(
            {
                "status": "failed",
                "message": f"工具不存在：{tool_name}",
                "error": {
                    "type": "tool_error",
                    "message": f"工具不存在：{tool_name}",
                    "detail": None,
                },
            }
        )
        return response

    verification_report = execute_verified_change(
        tool_name=tool_name,
        tool_args=tool_args,
        tool_func=tool_func,
        run_tests=_should_run_tests(task_plan),
    )

    delete_pending_action(approval_id)
    delete_pending_execution_context(approval_id)

    tool_execution = verification_report.get("tool_execution") or {}
    response["tool_result"] = str(tool_execution.get("result", ""))
    response["verification_report"] = verification_report

    approved_tool_step = _build_approved_tool_step(
        existing_steps=pre_approval_steps,
        tool_name=tool_name,
        tool_args=tool_args,
        risk_level=risk_level,
        tool_execution=tool_execution,
        verification_report=verification_report,
        policy_decision=policy_decision,
    )
    combined_initial_steps = [
        *pre_approval_steps,
        approved_tool_step,
    ]

    if not verification_report.get("success"):
        rollback = verification_report.get("rollback") or {}
        response.update(
            {
                "status": "verification_failed",
                "message": (
                    "写操作未通过验证，系统已自动恢复修改前状态。"
                    if rollback.get("success")
                    else "写操作未通过验证，并且自动恢复未完全成功，需要人工检查。"
                ),
                "success": False,
                "error": {
                    "type": "verification_failed",
                    "message": "写操作未通过修改后验证。",
                    "detail": verification_report.get("status"),
                },
            }
        )
        return response

    # 写步骤已通过验证，直接推进保存的 Executor State。
    mark_approved_step_completed(
        executor_state,
        tool_name=tool_name,
    )

    resume_context = (
        "高风险写步骤已经通过用户审批、二次策略检查和自动验证。\n"
        f"已完成工具：{tool_name}\n"
        f"真实变更路径：{verification_report.get('changed_paths', [])}\n"
        f"验证状态：{verification_report.get('status')}\n"
        f"新增行数：{verification_report.get('diff', {}).get('total_added_lines', 0)}\n"
        f"删除行数：{verification_report.get('diff', {}).get('total_removed_lines', 0)}\n"
        "请从 Executor State 的下一计划步骤继续；禁止重新调用已经完成的写工具。"
    )

    try:
        resume_result = runtime["run_agent_loop"](
            agent_name="CodeAgent",
            system_prompt=runtime["system_prompt"],
            tools=runtime["tools"],
            available_tools=runtime["available_tools"],
            user_message=original_user_message,
            max_steps=_remaining_model_rounds(
                executor_state,
                int(saved_context.get("max_steps") or 5),
            ),
            task_plan_override=task_plan,
            executor_state_override=executor_state,
            resume_context=resume_context,
            initial_steps=combined_initial_steps,
        )
    except Exception as error_value:
        resume_result = {
            "status": "failed",
            "answer": "写操作验证通过，但 Planner–Executor Resume 失败。",
            "task_plan": task_plan,
            "executor_state": executor_state,
            "steps": [],
            "error": {
                "type": "resume_error",
                "message": "审批后续跑失败。",
                "detail": str(error_value),
            },
        }

    response.update(
        {
            "status": "approved",
            "message": "写操作已执行并通过自动验证，Agent 已从原计划下一步骤继续运行。",
            "success": True,
            "error": None,
            "resume_result": resume_result,
        }
    )
    return response
