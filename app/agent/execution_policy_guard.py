from __future__ import annotations

from typing import Any

from app.agent.plan_parameter_audit import (
    COMMAND_ARGUMENT_KEYS,
    COMMAND_TOOL_NAMES,
    PRIMARY_PATH_ARGUMENT_KEYS,
    WRITE_TOOL_NAMES,
    command_mentions_target,
    deduplicate_keep_order,
    extract_planned_target_paths,
    flatten_string_values,
    normalize_audit_path,
    normalize_command_text,
    path_matches_any_scope,
)


POLICY_GUARD_VERSION = "1.0"

POLICY_ALLOW = "allow"
POLICY_ALLOW_WITH_AUDIT = "allow_with_audit"
POLICY_REQUIRE_APPROVAL = "require_approval"
POLICY_BLOCK = "block"

POLICY_VIOLATION_INVALID_TOOL = "invalid_tool"
POLICY_VIOLATION_UNPLANNED_RISKY_TOOL = "unplanned_risky_tool"
POLICY_VIOLATION_MISSING_WRITE_PATH = "missing_write_path"
POLICY_VIOLATION_MISSING_PLAN_SCOPE = "missing_plan_scope"
POLICY_VIOLATION_WRITE_OUTSIDE_SCOPE = "write_outside_scope"
POLICY_VIOLATION_MISSING_COMMAND = "missing_command"
POLICY_VIOLATION_UNPLANNED_COMMAND = "unplanned_command"
POLICY_VIOLATION_UNCLASSIFIED_HIGH_RISK = "unclassified_high_risk_tool"

RISK_LEVELS = {"low", "medium", "high"}


def normalize_risk_level(value: Any) -> str:
    """把风险等级统一成 low、medium、high。"""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in RISK_LEVELS else "low"


def extract_planned_tools(
    task_plan: dict[str, Any] | None,
) -> list[str]:
    """从 Planner 的 steps 和 suggested_tools 中提取计划工具。"""
    if not isinstance(task_plan, dict):
        return []

    result: list[str] = []

    plan_steps = task_plan.get("steps", [])
    if isinstance(plan_steps, list):
        for step in plan_steps:
            if not isinstance(step, dict):
                continue
            tool_name = step.get("suggested_tool")
            if isinstance(tool_name, str) and tool_name.strip():
                result.append(tool_name.strip())

    suggested_tools = task_plan.get("suggested_tools", [])
    if isinstance(suggested_tools, list):
        for tool_name in suggested_tools:
            if isinstance(tool_name, str) and tool_name.strip():
                result.append(tool_name.strip())

    return deduplicate_keep_order(result)


def extract_primary_paths(
    tool_args: dict[str, Any] | None,
) -> list[str]:
    """从工具参数中提取真正被操作的文件或目录路径。"""
    if not isinstance(tool_args, dict):
        return []

    result: list[str] = []

    for argument_name, raw_value in tool_args.items():
        if argument_name not in PRIMARY_PATH_ARGUMENT_KEYS:
            continue

        for string_value in flatten_string_values(raw_value):
            normalized = normalize_audit_path(string_value)
            if normalized:
                result.append(normalized)

    return deduplicate_keep_order(result)


def extract_command(
    tool_args: dict[str, Any] | None,
) -> str:
    """从 run_command 参数中提取命令文本。"""
    if not isinstance(tool_args, dict):
        return ""

    for key in COMMAND_ARGUMENT_KEYS:
        raw_command = tool_args.get(key)
        if isinstance(raw_command, str) and raw_command.strip():
            return normalize_command_text(raw_command)

    return ""


def build_violation(code: str, message: str) -> dict[str, str]:
    """构建结构化策略违规信息。"""
    return {
        "code": code,
        "message": message,
    }


def evaluate_tool_policy(
    *,
    task_plan: dict[str, Any] | None,
    tool_name: str,
    tool_args: dict[str, Any] | None,
    risk_level: str,
) -> dict[str, Any]:
    """
    在工具真正执行前进行计划范围检查。

    决策：
    - allow：允许执行；
    - allow_with_audit：允许，但记录计划外低风险扩展；
    - require_approval：范围合法，但仍需人工审批；
    - block：违反计划或无法证明安全，禁止执行。
    """
    normalized_tool_name = str(tool_name or "").strip()
    normalized_risk = normalize_risk_level(risk_level)
    planned_tools = extract_planned_tools(task_plan)
    planned_tool_set = set(planned_tools)
    planned_target_paths = extract_planned_target_paths(task_plan)
    requested_paths = extract_primary_paths(tool_args)
    command = extract_command(tool_args)

    planned = normalized_tool_name in planned_tool_set
    matched_paths = [
        path
        for path in requested_paths
        if path_matches_any_scope(path, planned_target_paths)
    ]
    outside_paths = [
        path
        for path in requested_paths
        if planned_target_paths
        and not path_matches_any_scope(path, planned_target_paths)
    ]

    reasons: list[str] = []
    violations: list[dict[str, str]] = []

    if not normalized_tool_name:
        violations.append(
            build_violation(
                POLICY_VIOLATION_INVALID_TOOL,
                "工具名称为空，无法执行策略检查。",
            )
        )

    is_write_tool = normalized_tool_name in WRITE_TOOL_NAMES
    is_command_tool = normalized_tool_name in COMMAND_TOOL_NAMES

    # 任何中高风险计划外工具默认禁止。低风险读取允许动态扩展。
    if (
        normalized_tool_name
        and normalized_risk in {"medium", "high"}
        and not planned
    ):
        code = (
            POLICY_VIOLATION_UNPLANNED_COMMAND
            if is_command_tool
            else POLICY_VIOLATION_UNPLANNED_RISKY_TOOL
        )
        violations.append(
            build_violation(
                code,
                f"工具 {normalized_tool_name} 不在 Planner 推荐工具中，"
                f"且风险等级为 {normalized_risk}。",
            )
        )

    if is_write_tool:
        if not requested_paths:
            violations.append(
                build_violation(
                    POLICY_VIOLATION_MISSING_WRITE_PATH,
                    "写工具没有可识别的目标路径参数，无法验证写入范围。",
                )
            )

        if not planned_target_paths:
            violations.append(
                build_violation(
                    POLICY_VIOLATION_MISSING_PLAN_SCOPE,
                    "Planner 没有提取目标路径，无法安全授权写操作。",
                )
            )

        if outside_paths:
            violations.append(
                build_violation(
                    POLICY_VIOLATION_WRITE_OUTSIDE_SCOPE,
                    "写入路径超出了 Planner 的目标范围："
                    + "、".join(outside_paths)
                    + "。",
                )
            )

    if is_command_tool:
        if not command:
            violations.append(
                build_violation(
                    POLICY_VIOLATION_MISSING_COMMAND,
                    "命令工具缺少 command/cmd 参数。",
                )
            )

    if normalized_risk == "high" and not is_write_tool:
        violations.append(
            build_violation(
                POLICY_VIOLATION_UNCLASSIFIED_HIGH_RISK,
                "发现尚未纳入写工具分类的高风险工具，按 Fail Closed 拦截。",
            )
        )

    if violations:
        decision = POLICY_BLOCK
        reasons.append("策略检查发现不可接受的执行风险，工具未执行。")
    elif is_write_tool:
        decision = POLICY_REQUIRE_APPROVAL
        reasons.append("写工具位于计划目标范围内，但仍需用户审批。")
    elif is_command_tool:
        decision = POLICY_ALLOW
        reasons.append("命令执行已被 Planner 纳入计划，交由命令工具白名单继续校验。")

        if planned_target_paths and not any(
            command_mentions_target(command, target)
            for target in planned_target_paths
        ):
            decision = POLICY_ALLOW_WITH_AUDIT
            reasons.append(
                "命令未直接提到计划目标路径，可能是项目级测试，允许执行但保留审计。"
            )
    elif requested_paths and planned_target_paths and outside_paths:
        decision = POLICY_ALLOW_WITH_AUDIT
        reasons.append(
            "低风险读取范围超出计划目标，可能用于依赖或上下文分析，允许并记录。"
        )
    elif not planned and normalized_risk == "low":
        decision = POLICY_ALLOW_WITH_AUDIT
        reasons.append("模型增加了计划外低风险工具，允许执行并记录偏离。")
    else:
        decision = POLICY_ALLOW
        reasons.append("工具及其参数符合当前执行策略。")

    return {
        "version": POLICY_GUARD_VERSION,
        "decision": decision,
        "allowed": decision in {
            POLICY_ALLOW,
            POLICY_ALLOW_WITH_AUDIT,
            POLICY_REQUIRE_APPROVAL,
        },
        "requires_approval": decision == POLICY_REQUIRE_APPROVAL,
        "tool_name": normalized_tool_name,
        "risk_level": normalized_risk,
        "planned": planned,
        "planned_tools": planned_tools,
        "planned_target_paths": planned_target_paths,
        "requested_paths": requested_paths,
        "matched_paths": matched_paths,
        "outside_paths": outside_paths,
        "command": command,
        "violations": violations,
        "reasons": reasons,
    }


def format_policy_feedback(policy_decision: dict[str, Any]) -> str:
    """生成可反馈给模型的策略结果，帮助模型安全地重新规划。"""
    decision = str(policy_decision.get("decision", ""))
    tool_name = str(policy_decision.get("tool_name", ""))
    reasons = policy_decision.get("reasons", [])
    violations = policy_decision.get("violations", [])

    lines = [
        "[Execution Policy Guard]",
        f"工具：{tool_name or '未知'}",
        f"决策：{decision or '未知'}",
    ]

    if isinstance(reasons, list):
        for reason in reasons:
            lines.append(f"原因：{reason}")

    if isinstance(violations, list):
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            lines.append(
                "违规："
                f"{violation.get('code', 'unknown')} - "
                f"{violation.get('message', '')}"
            )

    if decision == POLICY_BLOCK:
        lines.append("请改用计划内工具和目标路径，或先向用户说明需要重新规划。")

    return "\n".join(lines)
