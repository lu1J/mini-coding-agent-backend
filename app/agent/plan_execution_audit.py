from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.agent.plan_parameter_audit import build_parameter_audit
from app.agent.tool_policy import get_tool_risk_level


AUDIT_VERSION = "2.0"

AUDIT_STATUS_ALIGNED = "aligned"
AUDIT_STATUS_PARTIAL = "partially_aligned"
AUDIT_STATUS_DIVERGED = "diverged"
AUDIT_STATUS_REVIEW = "requires_review"
AUDIT_STATUS_NOT_EXECUTED = "not_executed"


RISK_LEVEL_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
}


def deduplicate_keep_order(items: list[str]) -> list[str]:
    """去重，同时保留第一次出现时的顺序。"""
    seen: set[str] = set()
    result: list[str] = []

    for item in items:
        normalized = str(item or "").strip()

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        result.append(normalized)

    return result


def normalize_risk_level(value: Any) -> str:
    """把风险等级统一为 low、medium 或 high。"""
    normalized = str(value or "").strip().lower()

    if normalized in RISK_LEVEL_ORDER:
        return normalized

    return "low"


def max_risk_level(levels: list[str]) -> str:
    """返回风险列表中的最高等级。"""
    if not levels:
        return "low"

    return max(
        (normalize_risk_level(level) for level in levels),
        key=lambda level: RISK_LEVEL_ORDER[level],
    )


def extract_planned_tools(task_plan: dict[str, Any] | None) -> list[str]:
    """
    从 task_plan 提取计划工具。

    优先读取带顺序的 steps[*].suggested_tool，
    再补充 suggested_tools 中没有出现的工具。
    """
    if not task_plan:
        return []

    tools: list[str] = []

    plan_steps = task_plan.get("steps", [])

    if isinstance(plan_steps, list):
        for step in plan_steps:
            if not isinstance(step, dict):
                continue

            tool_name = step.get("suggested_tool")

            if isinstance(tool_name, str) and tool_name.strip():
                tools.append(tool_name.strip())

    suggested_tools = task_plan.get("suggested_tools", [])

    if isinstance(suggested_tools, list):
        for tool_name in suggested_tools:
            if isinstance(tool_name, str) and tool_name.strip():
                tools.append(tool_name.strip())

    return deduplicate_keep_order(tools)


def extract_tool_call_steps(
    execution_steps: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """只保留真正已经执行的 tool_call 步骤。"""
    if not execution_steps:
        return []

    return [
        step
        for step in execution_steps
        if (
            isinstance(step, dict)
            and step.get("type") == "tool_call"
            and isinstance(step.get("tool_name"), str)
            and str(step.get("tool_name")).strip()
        )
    ]


def extract_executed_tools(
    execution_steps: list[dict[str, Any]] | None,
) -> list[str]:
    """按真实执行顺序提取工具名，并保留重复调用。"""
    return [
        str(step["tool_name"]).strip()
        for step in extract_tool_call_steps(execution_steps)
    ]


def longest_common_subsequence_length(
    first: list[str],
    second: list[str],
) -> int:
    """计算两个工具序列的最长公共子序列长度。"""
    if not first or not second:
        return 0

    previous_row = [0] * (len(second) + 1)

    for first_item in first:
        current_row = [0]

        for index, second_item in enumerate(second, start=1):
            if first_item == second_item:
                value = previous_row[index - 1] + 1
            else:
                value = max(
                    previous_row[index],
                    current_row[index - 1],
                )

            current_row.append(value)

        previous_row = current_row

    return previous_row[-1]


def safe_ratio(
    numerator: int,
    denominator: int,
    *,
    empty_value: float,
) -> float:
    """安全计算比例，避免除以零。"""
    if denominator == 0:
        return round(empty_value, 2)

    return round(numerator / denominator, 2)


def calculate_alignment_metrics(
    *,
    planned_tools: list[str],
    executed_tools: list[str],
) -> dict[str, float]:
    """
    计算计划与执行的一致性指标。

    coverage：计划工具中有多少真正执行。
    precision：实际工具中有多少来自计划。
    order_score：共同工具的相对顺序是否保持。
    alignment_score：三项加权总分。
    """
    planned_unique = deduplicate_keep_order(planned_tools)
    executed_unique = deduplicate_keep_order(executed_tools)

    planned_set = set(planned_unique)
    executed_set = set(executed_unique)
    matched_set = planned_set & executed_set

    coverage = safe_ratio(
        len(matched_set),
        len(planned_unique),
        empty_value=1.0 if not executed_unique else 0.0,
    )

    precision = safe_ratio(
        len(matched_set),
        len(executed_unique),
        empty_value=1.0,
    )

    common_planned_order = [
        tool for tool in planned_unique if tool in matched_set
    ]
    common_executed_order = [
        tool for tool in executed_unique if tool in matched_set
    ]

    if not common_planned_order:
        order_score = (
            1.0
            if not planned_unique and not executed_unique
            else 0.0
        )
    else:
        lcs_length = longest_common_subsequence_length(
            common_planned_order,
            common_executed_order,
        )
        order_score = safe_ratio(
            lcs_length,
            len(common_planned_order),
            empty_value=0.0,
        )

    alignment_score = round(
        coverage * 0.50
        + precision * 0.30
        + order_score * 0.20,
        2,
    )

    return {
        "coverage": coverage,
        "precision": precision,
        "order_score": order_score,
        "alignment_score": alignment_score,
    }


def extract_actual_tool_risks(
    execution_steps: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """提取每一次真实工具调用的风险等级。"""
    result: list[dict[str, str]] = []

    for step in extract_tool_call_steps(execution_steps):
        tool_name = str(step["tool_name"]).strip()
        step_risk = step.get("risk_level")

        if (
            isinstance(step_risk, str)
            and step_risk.lower() in RISK_LEVEL_ORDER
        ):
            risk_level = normalize_risk_level(step_risk)
        else:
            risk_level = normalize_risk_level(
                get_tool_risk_level(tool_name)
            )

        result.append(
            {
                "tool_name": tool_name,
                "risk_level": risk_level,
            }
        )

    return result


def calculate_risk_audit(
    *,
    task_plan: dict[str, Any] | None,
    planned_tools: list[str],
    execution_steps: list[dict[str, Any]] | None,
    unplanned_tools: list[str],
) -> dict[str, Any]:
    """比较 Planner 风险、计划工具风险和真实执行风险。"""
    task_plan = task_plan or {}

    declared_planned_risk = normalize_risk_level(
        task_plan.get("risk_level")
    )

    inferred_planned_risk = max_risk_level(
        [get_tool_risk_level(tool_name) for tool_name in planned_tools]
    )

    actual_tool_risks = extract_actual_tool_risks(execution_steps)
    actual_max_risk = max_risk_level(
        [item["risk_level"] for item in actual_tool_risks]
    )

    risk_escalated = (
        RISK_LEVEL_ORDER[actual_max_risk]
        > RISK_LEVEL_ORDER[declared_planned_risk]
    )

    unplanned_set = set(unplanned_tools)

    unplanned_risky_tools = deduplicate_keep_order(
        [
            item["tool_name"]
            for item in actual_tool_risks
            if (
                item["tool_name"] in unplanned_set
                and item["risk_level"] in {"medium", "high"}
            )
        ]
    )

    actual_high_risk_tools = deduplicate_keep_order(
        [
            item["tool_name"]
            for item in actual_tool_risks
            if item["risk_level"] == "high"
        ]
    )

    approval_expected_by_plan = bool(
        task_plan.get("needs_approval", False)
    )

    approval_expectation_mismatch = bool(
        actual_high_risk_tools
        and not approval_expected_by_plan
    )

    return {
        "declared_planned_risk": declared_planned_risk,
        "inferred_planned_risk": inferred_planned_risk,
        "planned_risk_inconsistent": (
            declared_planned_risk != inferred_planned_risk
        ),
        "actual_max_risk": actual_max_risk,
        "risk_escalated": risk_escalated,
        "approval_expected_by_plan": approval_expected_by_plan,
        "actual_high_risk_tools": actual_high_risk_tools,
        "approval_expectation_mismatch": (
            approval_expectation_mismatch
        ),
        "unplanned_risky_tools": unplanned_risky_tools,
        "actual_tool_risks": actual_tool_risks,
    }


def calculate_execution_outcome(
    execution_steps: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """汇总工具成功、失败、重试和审批等待情况。"""
    steps = execution_steps or []
    tool_steps = extract_tool_call_steps(steps)

    successful_tool_calls = sum(
        1 for step in tool_steps if step.get("success") is True
    )
    failed_tool_calls = sum(
        1 for step in tool_steps if step.get("success") is False
    )
    retry_tool_calls = sum(
        1
        for step in tool_steps
        if step.get("retry_from_reflection") is True
    )
    approval_required_count = sum(
        1
        for step in steps
        if isinstance(step, dict)
        and step.get("type") == "approval_required"
    )
    final_answer_present = any(
        isinstance(step, dict)
        and step.get("type") == "final_answer"
        for step in steps
    )

    executed_tool_counts = dict(
        Counter(extract_executed_tools(steps))
    )

    return {
        "tool_call_count": len(tool_steps),
        "successful_tool_calls": successful_tool_calls,
        "failed_tool_calls": failed_tool_calls,
        "retry_tool_calls": retry_tool_calls,
        "approval_required_count": approval_required_count,
        "final_answer_present": final_answer_present,
        "executed_tool_counts": executed_tool_counts,
    }


def determine_audit_status(
    *,
    executed_tools: list[str],
    metrics: dict[str, float],
    risk: dict[str, Any],
    unplanned_tools: list[str],
    unused_planned_tools: list[str],
) -> str:
    """根据一致性和风险确定最终审计状态。"""
    if not executed_tools:
        return AUDIT_STATUS_NOT_EXECUTED

    if (
        risk["risk_escalated"]
        or risk["approval_expectation_mismatch"]
        or risk["unplanned_risky_tools"]
    ):
        return AUDIT_STATUS_REVIEW

    if (
        metrics["alignment_score"] >= 0.95
        and not unplanned_tools
        and not unused_planned_tools
    ):
        return AUDIT_STATUS_ALIGNED

    if metrics["alignment_score"] >= 0.60:
        return AUDIT_STATUS_PARTIAL

    return AUDIT_STATUS_DIVERGED


def build_deviation_notes(
    *,
    unplanned_tools: list[str],
    unused_planned_tools: list[str],
    risk: dict[str, Any],
) -> list[str]:
    """生成便于人类阅读的偏离说明。"""
    notes: list[str] = []

    low_risk_unplanned = [
        tool_name
        for tool_name in unplanned_tools
        if normalize_risk_level(
            get_tool_risk_level(tool_name)
        ) == "low"
    ]

    if low_risk_unplanned:
        notes.append(
            "实际执行增加了计划外的低风险只读工具："
            + "、".join(low_risk_unplanned)
            + "。这通常表示模型根据中间结果补充了读取或搜索步骤。"
        )

    if unused_planned_tools:
        notes.append(
            "以下计划工具未实际使用："
            + "、".join(unused_planned_tools)
            + "。模型可能提前获得了足够信息。"
        )

    if risk["risk_escalated"]:
        notes.append(
            "实际最高风险高于 Planner 声明的计划风险，需要重点审查。"
        )

    if risk["approval_expectation_mismatch"]:
        notes.append(
            "计划没有预期人工审批，但真实工具步骤中出现了高风险工具。"
        )

    if risk["unplanned_risky_tools"]:
        notes.append(
            "发现计划外的中高风险工具："
            + "、".join(risk["unplanned_risky_tools"])
            + "。"
        )

    return notes


def build_audit_summary(
    *,
    status: str,
    planned_tools: list[str],
    executed_tools: list[str],
    unplanned_tools: list[str],
    unused_planned_tools: list[str],
    risk: dict[str, Any],
) -> str:
    """生成简短审计摘要。"""
    summary = (
        f"计划工具 {len(planned_tools)} 个，"
        f"实际调用 {len(executed_tools)} 次；"
        f"审计状态为 {status}。"
    )

    if unplanned_tools:
        summary += " 计划外工具：" + "、".join(unplanned_tools) + "。"

    if unused_planned_tools:
        summary += (
            " 未使用的计划工具："
            + "、".join(unused_planned_tools)
            + "。"
        )

    if risk["risk_escalated"]:
        summary += " 实际执行发生风险升级。"
    else:
        summary += " 未发现计划风险升级。"

    return summary


def build_plan_execution_audit(
    *,
    task_plan: dict[str, Any] | None,
    execution_steps: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """构建完整的计划—执行一致性审计。"""
    planned_tools = extract_planned_tools(task_plan)
    executed_tools = extract_executed_tools(execution_steps)

    planned_unique = deduplicate_keep_order(planned_tools)
    executed_unique = deduplicate_keep_order(executed_tools)

    planned_set = set(planned_unique)
    executed_set = set(executed_unique)

    matched_tools = [
        tool_name
        for tool_name in planned_unique
        if tool_name in executed_set
    ]
    unused_planned_tools = [
        tool_name
        for tool_name in planned_unique
        if tool_name not in executed_set
    ]
    unplanned_tools = [
        tool_name
        for tool_name in executed_unique
        if tool_name not in planned_set
    ]

    metrics = calculate_alignment_metrics(
        planned_tools=planned_tools,
        executed_tools=executed_tools,
    )
    risk = calculate_risk_audit(
        task_plan=task_plan,
        planned_tools=planned_tools,
        execution_steps=execution_steps,
        unplanned_tools=unplanned_tools,
    )
    outcome = calculate_execution_outcome(execution_steps)
    parameter_audit = build_parameter_audit(
        task_plan=task_plan,
        execution_steps=execution_steps,
        planned_tools=planned_tools,
    )

    status = determine_audit_status(
        executed_tools=executed_tools,
        metrics=metrics,
        risk=risk,
        unplanned_tools=unplanned_tools,
        unused_planned_tools=unused_planned_tools,
    )

    # 参数级审计比单纯工具名一致性更具体：
    # - 计划外写路径；
    # - 未计划命令执行；
    # - 无法确认目标的写工具；
    # 都必须升级为 requires_review。
    if parameter_audit["requires_review"]:
        status = AUDIT_STATUS_REVIEW
    elif (
        status == AUDIT_STATUS_ALIGNED
        and parameter_audit["expanded_read_scope"]
    ):
        status = AUDIT_STATUS_PARTIAL

    deviation_notes = build_deviation_notes(
        unplanned_tools=unplanned_tools,
        unused_planned_tools=unused_planned_tools,
        risk=risk,
    )
    deviation_notes.extend(parameter_audit["notes"])

    summary = build_audit_summary(
        status=status,
        planned_tools=planned_unique,
        executed_tools=executed_tools,
        unplanned_tools=unplanned_tools,
        unused_planned_tools=unused_planned_tools,
        risk=risk,
    )

    return {
        "version": AUDIT_VERSION,
        "status": status,
        "planned_tools": planned_unique,
        "executed_tools": executed_tools,
        "executed_unique_tools": executed_unique,
        "matched_tools": matched_tools,
        "unused_planned_tools": unused_planned_tools,
        "unplanned_tools": unplanned_tools,
        "unplanned_risky_tools": risk["unplanned_risky_tools"],
        "metrics": metrics,
        "risk": risk,
        "outcome": outcome,
        "parameter_audit": parameter_audit,
        "deviation_notes": deviation_notes,
        "summary": (
            summary
            + " 参数审计状态为 "
            + parameter_audit["status"]
            + "。"
        ),
    }


def attach_plan_execution_audit(
    result: dict[str, Any],
) -> dict[str, Any]:
    """给 Agent 最终结果附加计划—执行审计。"""
    result["plan_execution_audit"] = build_plan_execution_audit(
        task_plan=result.get("task_plan"),
        execution_steps=result.get("steps", []),
    )
    return result



def persist_audit_to_run_log(
    *,
    log_info: dict[str, Any] | None,
    plan_execution_audit: dict[str, Any],
) -> str | None:
    """
    把审计结果补写到 run_logger 已生成的 JSON Trace 中。

    这样即使旧版 save_agent_run 暂时没有
    plan_execution_audit 参数，Trace 中仍然能保存审计结果。

    成功返回 None；失败返回错误文本，
    但不会影响 Agent 主任务结果。
    """
    if not isinstance(log_info, dict):
        return None

    raw_log_path = log_info.get("log_path")

    if not isinstance(raw_log_path, str) or not raw_log_path.strip():
        return None

    try:
        from app.tools.file_tools import WORKSPACE_ROOT

        workspace_root = Path(WORKSPACE_ROOT).resolve()
        normalized_log_path = raw_log_path.replace("\\", "/")
        target_path = (
            workspace_root / normalized_log_path
        ).resolve()

        # 防止日志路径意外跳出 workspace。
        target_path.relative_to(workspace_root)

        if not target_path.exists() or not target_path.is_file():
            return (
                "未找到需要补写审计信息的 Trace 文件："
                f"{target_path}"
            )

        data = json.loads(
            target_path.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            return "Trace 文件根节点不是 JSON object。"

        data["plan_execution_audit"] = plan_execution_audit

        target_path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return None

    except Exception as error:
        return f"审计信息写入 Trace 失败：{str(error)}"
