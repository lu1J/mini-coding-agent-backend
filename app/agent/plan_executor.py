from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from typing import Any


PLAN_EXECUTOR_VERSION = "1.1"

EXECUTOR_STATUS_RUNNING = "running"
EXECUTOR_STATUS_WAITING_APPROVAL = "waiting_approval"
EXECUTOR_STATUS_READY_FOR_FINAL = "ready_for_final"
EXECUTOR_STATUS_COMPLETED = "completed"
EXECUTOR_STATUS_FAILED = "failed"
EXECUTOR_STATUS_MAX_STEPS = "max_steps_reached"

STEP_STATUS_PENDING = "pending"
STEP_STATUS_RUNNING = "running"
STEP_STATUS_WAITING_APPROVAL = "waiting_approval"
STEP_STATUS_RETRYING = "retrying"
STEP_STATUS_COMPLETED = "completed"
STEP_STATUS_COMPLETED_WITH_WARNINGS = "completed_with_warnings"
STEP_STATUS_FAILED = "failed"
STEP_STATUS_SKIPPED = "skipped"

EXECUTOR_ALLOW_CURRENT = "allow_current_step"
EXECUTOR_ALLOW_SUPPORTING = "allow_supporting_tool"
EXECUTOR_BLOCK_OUT_OF_ORDER = "block_out_of_order"
EXECUTOR_BLOCK_FINAL_PHASE = "block_final_phase"

# 与计划工具语义接近、可以完成同一个计划步骤的工具。
TOOL_EQUIVALENTS: dict[str, set[str]] = {
    "read_file": {"read_file", "read_file_lines"},
    "read_file_lines": {"read_file_lines", "read_file"},
    "search_code": {"search_code", "search_python_symbol"},
    "search_python_symbol": {"search_python_symbol", "search_code"},
    "get_workspace_diff": {
        "get_workspace_diff",
        "get_file_diff",
        "get_git_diff",
    },
    "get_file_diff": {
        "get_file_diff",
        "get_workspace_diff",
        "get_git_diff",
    },
}

# 低风险辅助工具不会完成当前步骤，但每个计划步骤最多允许一次。
SUPPORTING_LOW_RISK_TOOLS = {
    "list_files",
    "read_file",
    "read_file_lines",
    "search_code",
    "search_python_symbol",
    "get_python_file_outline",
    "get_python_dependencies",
    "analyze_python_impact",
    "get_git_status",
    "get_git_diff",
    "get_file_diff",
    "get_workspace_diff",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _allowed_tools_for_step(suggested_tool: str | None) -> list[str]:
    if not suggested_tool:
        return []

    tools = TOOL_EQUIVALENTS.get(
        suggested_tool,
        {suggested_tool},
    )
    return sorted(tools)


def create_executor_state(
    task_plan: dict[str, Any],
) -> dict[str, Any]:
    """把 Planner 的静态步骤转换为可运行状态机。"""
    runtime_steps: list[dict[str, Any]] = []

    for raw_step in task_plan.get("steps", []):
        if not isinstance(raw_step, dict):
            continue

        suggested_tool = raw_step.get("suggested_tool")
        runtime_steps.append(
            {
                "plan_step_index": int(
                    raw_step.get("index")
                    or len(runtime_steps) + 1
                ),
                "title": str(raw_step.get("title") or "未命名步骤"),
                "description": str(raw_step.get("description") or ""),
                "suggested_tool": suggested_tool,
                "allowed_tools": _allowed_tools_for_step(
                    str(suggested_tool) if suggested_tool else None
                ),
                "risk_level": str(raw_step.get("risk_level") or "low"),
                "reason": str(raw_step.get("reason") or ""),
                "status": STEP_STATUS_PENDING,
                "attempts": 0,
                "supporting_calls_used": 0,
                "actual_tool_name": None,
                "last_error": None,
                "attempt_history": [],
                "warnings": [],
                "started_at": None,
                "completed_at": None,
            }
        )

    state = {
        "version": PLAN_EXECUTOR_VERSION,
        "status": EXECUTOR_STATUS_RUNNING,
        "objective": str(task_plan.get("objective") or ""),
        "target_paths": list(task_plan.get("target_paths") or []),
        "current_step_position": 0,
        "total_steps": len(runtime_steps),
        "completed_steps": 0,
        "warning_steps": 0,
        "completion_status": "in_progress",
        "blocked_calls": 0,
        "supporting_calls": 0,
        "steps": runtime_steps,
        "history": [],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    _refresh_executor_status(state)
    return state


def clone_executor_state(
    executor_state: dict[str, Any],
) -> dict[str, Any]:
    return deepcopy(executor_state)


def get_current_executor_step(
    executor_state: dict[str, Any],
) -> dict[str, Any] | None:
    steps = executor_state.get("steps") or []
    position = int(executor_state.get("current_step_position") or 0)

    if position < 0 or position >= len(steps):
        return None

    step = steps[position]
    return step if isinstance(step, dict) else None


def _refresh_executor_status(
    executor_state: dict[str, Any],
) -> None:
    steps = executor_state.get("steps") or []
    position = int(executor_state.get("current_step_position") or 0)

    completed_statuses = {
        STEP_STATUS_COMPLETED,
        STEP_STATUS_COMPLETED_WITH_WARNINGS,
    }
    executor_state["completed_steps"] = sum(
        1
        for step in steps
        if isinstance(step, dict)
        and step.get("status") in completed_statuses
    )
    executor_state["warning_steps"] = sum(
        1
        for step in steps
        if isinstance(step, dict)
        and step.get("status") == STEP_STATUS_COMPLETED_WITH_WARNINGS
    )

    if not steps or position >= len(steps):
        executor_state["status"] = EXECUTOR_STATUS_COMPLETED
        executor_state["completion_status"] = (
            "completed_with_warnings"
            if executor_state.get("warning_steps")
            else "completed"
        )
    else:
        executor_state["completion_status"] = "in_progress"
        current = steps[position]
        if current.get("status") == STEP_STATUS_WAITING_APPROVAL:
            executor_state["status"] = EXECUTOR_STATUS_WAITING_APPROVAL
        elif not current.get("suggested_tool"):
            executor_state["status"] = EXECUTOR_STATUS_READY_FOR_FINAL
        elif executor_state.get("status") not in {
            EXECUTOR_STATUS_FAILED,
            EXECUTOR_STATUS_MAX_STEPS,
        }:
            executor_state["status"] = EXECUTOR_STATUS_RUNNING

    executor_state["updated_at"] = _now_iso()


def build_executor_instruction(
    executor_state: dict[str, Any],
) -> str:
    """生成只针对当前计划步骤的模型约束说明。"""
    current = get_current_executor_step(executor_state)

    if current is None:
        return (
            "[Planner–Executor]\n"
            "所有计划步骤已经完成。请直接生成最终总结，不要再调用工具。"
        )

    suggested_tool = current.get("suggested_tool")
    allowed_tools = current.get("allowed_tools") or []

    if not suggested_tool:
        return (
            "[Planner–Executor]\n"
            f"当前计划步骤 {current['plan_step_index']}：{current['title']}。\n"
            "这是最终汇总步骤。请根据已有工具结果直接回答，禁止继续调用工具。"
        )

    return (
        "[Planner–Executor]\n"
        f"当前只执行计划步骤 {current['plan_step_index']}：{current['title']}。\n"
        f"步骤说明：{current.get('description') or '无'}\n"
        f"首选工具：{suggested_tool}\n"
        f"可完成本步骤的工具：{', '.join(allowed_tools)}\n"
        "不要提前执行后续步骤。每个步骤最多只允许一次额外的低风险辅助探索。"
    )


def evaluate_executor_tool_call(
    *,
    executor_state: dict[str, Any],
    tool_name: str,
    risk_level: str,
    tool_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在 Policy Guard 之前检查工具是否符合当前计划步骤。"""
    current = get_current_executor_step(executor_state)

    base = {
        "version": PLAN_EXECUTOR_VERSION,
        "tool_name": tool_name,
        "risk_level": risk_level,
        "current_plan_step_index": (
            current.get("plan_step_index") if current else None
        ),
        "current_plan_step_title": (
            current.get("title") if current else None
        ),
        "expected_tool": (
            current.get("suggested_tool") if current else None
        ),
        "allowed_tools": (
            list(current.get("allowed_tools") or []) if current else []
        ),
        "completes_current_step": False,
        "allowed": False,
        "reason": "",
    }

    if current is None or not current.get("suggested_tool"):
        return {
            **base,
            "decision": EXECUTOR_BLOCK_FINAL_PHASE,
            "reason": "当前已经进入最终总结阶段，不允许继续调用工具。",
        }

    allowed_tools = set(current.get("allowed_tools") or [])
    if tool_name in allowed_tools:
        return {
            **base,
            "decision": EXECUTOR_ALLOW_CURRENT,
            "allowed": True,
            "completes_current_step": True,
            "reason": "工具符合当前计划步骤。",
        }

    supporting_used = int(current.get("supporting_calls_used") or 0)
    if (
        risk_level == "low"
        and tool_name in SUPPORTING_LOW_RISK_TOOLS
        and supporting_used < 1
    ):
        normalized_args = tool_args or {}
        requested_path = str(normalized_args.get("path") or ".").strip()
        if (
            current.get("suggested_tool") == "run_command"
            and tool_name == "list_files"
            and requested_path in {"", ".", "./", ".\\"}
        ):
            return {
                **base,
                "decision": EXECUTOR_BLOCK_OUT_OF_ORDER,
                "reason": (
                    "验证步骤失败后不允许遍历 workspace 根目录。"
                    "请直接修正命令，或只检查任务目标目录。"
                ),
            }

        return {
            **base,
            "decision": EXECUTOR_ALLOW_SUPPORTING,
            "allowed": True,
            "completes_current_step": False,
            "reason": "允许一次低风险辅助探索，但不会推进计划步骤。",
        }

    return {
        **base,
        "decision": EXECUTOR_BLOCK_OUT_OF_ORDER,
        "reason": (
            f"当前步骤要求 {current.get('suggested_tool')}，"
            f"工具 {tool_name} 属于越序或重复调用。"
        ),
    }


def record_executor_block(
    executor_state: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    executor_state["blocked_calls"] = int(
        executor_state.get("blocked_calls") or 0
    ) + 1
    executor_state.setdefault("history", []).append(
        {
            "event": "tool_blocked",
            "decision": deepcopy(decision),
            "at": _now_iso(),
        }
    )
    executor_state["updated_at"] = _now_iso()


def mark_supporting_tool_used(
    executor_state: dict[str, Any],
    *,
    tool_name: str,
) -> None:
    current = get_current_executor_step(executor_state)
    if current is None:
        return

    current["supporting_calls_used"] = int(
        current.get("supporting_calls_used") or 0
    ) + 1
    executor_state["supporting_calls"] = int(
        executor_state.get("supporting_calls") or 0
    ) + 1
    executor_state.setdefault("history", []).append(
        {
            "event": "supporting_tool_used",
            "plan_step_index": current.get("plan_step_index"),
            "tool_name": tool_name,
            "at": _now_iso(),
        }
    )
    executor_state["updated_at"] = _now_iso()


def mark_current_step_waiting_approval(
    executor_state: dict[str, Any],
    *,
    tool_name: str,
) -> None:
    current = get_current_executor_step(executor_state)
    if current is None:
        return

    current["status"] = STEP_STATUS_WAITING_APPROVAL
    current["actual_tool_name"] = tool_name
    current["attempts"] = int(current.get("attempts") or 0) + 1
    current["started_at"] = current.get("started_at") or _now_iso()
    executor_state.setdefault("history", []).append(
        {
            "event": "waiting_approval",
            "plan_step_index": current.get("plan_step_index"),
            "tool_name": tool_name,
            "at": _now_iso(),
        }
    )
    _refresh_executor_status(executor_state)


def _command_from_tool_args(tool_args: dict[str, Any] | None) -> str:
    if not isinstance(tool_args, dict):
        return ""
    return str(tool_args.get("command") or tool_args.get("cmd") or "").strip()


def _is_test_command(command: str) -> bool:
    lower = command.lower()
    return "pytest" in lower or "unittest" in lower


def mark_current_step_result(
    executor_state: dict[str, Any],
    *,
    tool_name: str,
    success: bool,
    error: dict[str, Any] | None = None,
    tool_args: dict[str, Any] | None = None,
) -> None:
    current = get_current_executor_step(executor_state)
    if current is None:
        return

    current["actual_tool_name"] = tool_name
    current["attempts"] = int(current.get("attempts") or 0) + 1
    current["started_at"] = current.get("started_at") or _now_iso()
    current["last_error"] = deepcopy(error)

    command = _command_from_tool_args(tool_args)
    attempt_record = {
        "tool_name": tool_name,
        "tool_args": deepcopy(tool_args or {}),
        "command": command,
        "success": success,
        "error": deepcopy(error),
        "at": _now_iso(),
    }
    current.setdefault("attempt_history", []).append(attempt_record)

    if success:
        previous_failed_test = any(
            isinstance(item, dict)
            and item.get("success") is False
            and _is_test_command(str(item.get("command") or ""))
            for item in current.get("attempt_history", [])[:-1]
        )
        current_command_is_test = _is_test_command(command)

        if (
            current.get("suggested_tool") == "run_command"
            and previous_failed_test
            and not current_command_is_test
        ):
            current["status"] = STEP_STATUS_COMPLETED_WITH_WARNINGS
            warning = (
                "用户要求的测试命令执行失败；后续仅完成了其他验证命令，"
                "因此本步骤按 completed_with_warnings 结束。"
            )
            current.setdefault("warnings", []).append(warning)
            event = "step_completed_with_warnings"
        else:
            current["status"] = STEP_STATUS_COMPLETED
            event = "step_completed"

        current["completed_at"] = _now_iso()
        executor_state["current_step_position"] = int(
            executor_state.get("current_step_position") or 0
        ) + 1
    else:
        current["status"] = STEP_STATUS_RETRYING
        event = "step_failed_retrying"

    executor_state.setdefault("history", []).append(
        {
            "event": event,
            "plan_step_index": current.get("plan_step_index"),
            "tool_name": tool_name,
            "success": success,
            "command": command,
            "at": _now_iso(),
        }
    )
    _refresh_executor_status(executor_state)

def mark_approved_step_completed(
    executor_state: dict[str, Any],
    *,
    tool_name: str,
) -> None:
    """审批后的写工具已执行并验证成功，推进到下一计划步骤。"""
    current = get_current_executor_step(executor_state)
    if current is None:
        return

    current["actual_tool_name"] = tool_name
    current["status"] = STEP_STATUS_COMPLETED
    current["completed_at"] = _now_iso()
    executor_state["current_step_position"] = int(
        executor_state.get("current_step_position") or 0
    ) + 1
    executor_state.setdefault("history", []).append(
        {
            "event": "approved_step_completed",
            "plan_step_index": current.get("plan_step_index"),
            "tool_name": tool_name,
            "at": _now_iso(),
        }
    )
    _refresh_executor_status(executor_state)


def can_accept_final_answer(
    executor_state: dict[str, Any],
) -> bool:
    current = get_current_executor_step(executor_state)
    return current is None or not current.get("suggested_tool")


def can_accept_degraded_final_answer(
    executor_state: dict[str, Any],
) -> bool:
    """
    当前计划步骤已经真实执行失败后，允许模型给出失败总结。

    这与“模型完全没有执行计划就提前回答”不同：
    - 至少调用过当前步骤工具；
    - 当前步骤处于 retrying；
    - last_error 中保留结构化失败原因。
    """
    current = get_current_executor_step(executor_state)
    if current is None:
        return False

    return (
        current.get("status") == STEP_STATUS_RETRYING
        and int(current.get("attempts") or 0) >= 1
        and isinstance(current.get("last_error"), dict)
    )


def mark_degraded_final_answer_completed(
    executor_state: dict[str, Any],
) -> None:
    """
    将无法继续完成的当前步骤标记为 failed，
    后续尚未执行的步骤标记为 skipped，并结束本次 Agent 交互。
    """
    steps = executor_state.get("steps") or []
    position = int(executor_state.get("current_step_position") or 0)

    if 0 <= position < len(steps):
        current = steps[position]
        if isinstance(current, dict):
            current["status"] = STEP_STATUS_FAILED
            current["completed_at"] = _now_iso()

        for step in steps[position + 1 :]:
            if (
                isinstance(step, dict)
                and step.get("status") == STEP_STATUS_PENDING
            ):
                step["status"] = STEP_STATUS_SKIPPED
                step["completed_at"] = _now_iso()

    executor_state["current_step_position"] = len(steps)
    executor_state["status"] = EXECUTOR_STATUS_COMPLETED
    executor_state.setdefault("history", []).append(
        {
            "event": "degraded_final_answer_completed",
            "at": _now_iso(),
        }
    )
    _refresh_executor_status(executor_state)


def mark_final_answer_completed(
    executor_state: dict[str, Any],
) -> None:
    current = get_current_executor_step(executor_state)

    if current is not None and not current.get("suggested_tool"):
        current["status"] = STEP_STATUS_COMPLETED
        current["completed_at"] = _now_iso()
        executor_state["current_step_position"] = int(
            executor_state.get("current_step_position") or 0
        ) + 1

    executor_state["status"] = EXECUTOR_STATUS_COMPLETED
    executor_state.setdefault("history", []).append(
        {
            "event": "final_answer_completed",
            "at": _now_iso(),
        }
    )
    _refresh_executor_status(executor_state)


def mark_executor_max_steps(
    executor_state: dict[str, Any],
) -> None:
    executor_state["status"] = EXECUTOR_STATUS_MAX_STEPS
    executor_state["updated_at"] = _now_iso()


def persist_executor_state_to_run_log(
    *,
    log_info: dict[str, Any] | None,
    executor_state: dict[str, Any],
) -> str | None:
    """兼容旧版 run_logger，把 Executor State 补写到 JSON Trace。"""
    if not isinstance(log_info, dict):
        return None

    raw_log_path = log_info.get("log_path")
    if not isinstance(raw_log_path, str) or not raw_log_path.strip():
        return None

    try:
        from app.tools.file_tools import WORKSPACE_ROOT

        workspace_root = Path(WORKSPACE_ROOT).resolve()
        target_path = (
            workspace_root / raw_log_path.replace("\\", "/")
        ).resolve()
        target_path.relative_to(workspace_root)

        if not target_path.exists() or not target_path.is_file():
            return f"未找到需要补写 Executor State 的 Trace：{target_path}"

        data = json.loads(target_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return "Trace 文件根节点不是 JSON object。"

        data["executor_state"] = executor_state
        target_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return None
    except Exception as error:
        return f"Executor State 写入 Trace 失败：{str(error)}"
