"""确定性评分器（纯函数）。

输入：
- task：golden_v1.json 中的单个任务定义；
- trace：runner 保存的真实 Agent 原始响应（含 HTTP 错误记录）；
- evidence：runner 保存的文件证据（before / approval / after）；
- duration_ms：runner 实测耗时（可空，offline 重评分时沿用归档值）。

输出：
- metrics：A..L 共 12 项指标；
- checks：逐条检查（含 required 标记与中文说明）；
- passed / errors / verdict。

约束：
- 不调用 DeepSeek；
- 不发送 HTTP；
- 不读取真实 workspace——文件事实只来自 evidence 参数。
"""

from __future__ import annotations

from typing import Any

# 12 项指标：A task_success / B required_tools_satisfied /
# C forbidden_tools_avoided / D plan_completion / E approval_correctness /
# F protocol_errors / G guardrail_interventions / H premature_final_count /
# I executor_stalled / J tool_call_count / K duration_ms / L final_status。

RUNNING_TOOL_RECORD_TYPES = {"tool_call"}
BLOCKED_RECORD_TYPES = {"executor_blocked", "policy_blocked"}

_TOOL_BLOCK_ERROR_HINTS = {
    "executor_blocked": "plan_step_violation",
    "policy_blocked": "policy_violation",
}


def _norm_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n")


def _iter_phase_steps(trace: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    if phase == "resumes":
        steps: list[dict[str, Any]] = []
        for response in trace.get("resumes") or []:
            if not isinstance(response, dict):
                continue
            for step in response.get("steps") or []:
                if isinstance(step, dict):
                    steps.append(step)
        return steps
    response = trace.get(phase)
    if not isinstance(response, dict):
        return []
    return [step for step in response.get("steps") or [] if isinstance(step, dict)]


def iter_steps(trace: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """按 phase 顺序返回 (phase 名, step)。"""
    output: list[tuple[str, dict[str, Any]]] = []
    for phase in ("invoke", "resumes"):
        for step in _iter_phase_steps(trace, phase):
            output.append((phase, step))
    return output


def iter_responses(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """按 phase 顺序返回所有顶层响应（invoke + resumes，仅字典）。"""
    responses: list[dict[str, Any]] = []
    invoke = trace.get("invoke")
    if isinstance(invoke, dict):
        responses.append(invoke)
    responses.extend(
        response for response in trace.get("resumes") or [] if isinstance(response, dict)
    )
    return responses


def last_response(trace: dict[str, Any]) -> dict[str, Any] | None:
    resumes = trace.get("resumes") or []
    if resumes:
        return resumes[-1] if isinstance(resumes[-1], dict) else None
    invoke = trace.get("invoke")
    return invoke if isinstance(invoke, dict) else None


def final_status(trace: dict[str, Any]) -> str | None:
    response = last_response(trace)
    if response is None:
        return None
    return response.get("status")


# ------------------------------------------------------------------ 底层信号


def count_records(
    trace: dict[str, Any],
    *,
    step_types: set[str] | None = None,
    error_types: set[str] | None = None,
) -> int:
    count = 0
    for _, step in iter_steps(trace):
        if step_types is not None and step.get("type") not in step_types:
            continue
        if error_types is not None:
            error = step.get("error")
            error_type = error.get("type") if isinstance(error, dict) else None
            if error_type not in error_types:
                continue
        count += 1
    return count


def executed_tool_records(
    trace: dict[str, Any],
    *,
    tool_names: set[str] | None = None,
    success: bool | None = None,
) -> list[dict[str, Any]]:
    """真实执行的工具调用记录（type == tool_call，含审批执行补记）。"""
    output: list[dict[str, Any]] = []
    for _, step in iter_steps(trace):
        if step.get("type") not in RUNNING_TOOL_RECORD_TYPES:
            continue
        tool_name = step.get("tool_name")
        if not tool_name:
            continue
        if tool_names is not None and tool_name not in tool_names:
            continue
        if success is not None and step.get("success") is not success:
            continue
        output.append(step)
    return output


def attempted_tool_names(trace: dict[str, Any]) -> set[str]:
    """模型尝试过的工具（执行、被拦、等待审批都算尝试）。"""
    names: set[str] = set()
    for _, step in iter_steps(trace):
        if step.get("tool_name"):
            names.add(str(step["tool_name"]))
    return names


def tool_result_text(record: dict[str, Any]) -> str:
    value = record.get("tool_result")
    if isinstance(value, dict):
        value = value.get("result", value)
    return _norm_text(value)


def _record_has_tool_result_patterns(
    record: dict[str, Any],
    patterns: list[str],
) -> bool:
    text = tool_result_text(record)
    return all(pattern in text for pattern in patterns)


def _args_basename_hit(record: dict[str, Any], path_hint: str) -> bool:
    if not path_hint:
        return True
    hint = path_hint
    args = record.get("tool_args")
    args_text = _norm_text(args) if isinstance(args, (str, dict)) else ""
    if hint in args_text:
        return True
    text = tool_result_text(record)
    if f"{hint}:" in text or hint in text:
        return True
    return False


def executor_state_of(trace: dict[str, Any]) -> dict[str, Any] | None:
    response = last_response(trace)
    if response is None:
        return None
    state = response.get("executor_state")
    return state if isinstance(state, dict) else None


def plan_completion(trace: dict[str, Any]) -> float | None:
    state = executor_state_of(trace)
    if state is None:
        return None
    total = int(state.get("total_steps") or 0)
    completed = int(state.get("completed_steps") or 0)
    if total <= 0:
        return None
    return completed / total


def guardrail_interventions(trace: dict[str, Any]) -> int:
    return count_records(trace, step_types=set(BLOCKED_RECORD_TYPES))


def premature_final_count(trace: dict[str, Any]) -> int:
    return count_records(trace, error_types={"premature_final_answer"})


def executor_stalled(trace: dict[str, Any]) -> bool:
    for response in iter_responses(trace):
        error = response.get("error")
        if isinstance(error, dict) and error.get("type") == "executor_stalled":
            return True
    return False


def protocol_errors(trace: dict[str, Any]) -> int:
    """真正可观察的协议错误数。

    只统计能从 trace/runner 明确识别的项：
    1. HTTP 非 200（含 resume 400 等）——runner 记录在 trace.http_errors；
    2. 审批上下文不完整（approval_context_invalid）——状态机内部的协议异常。
    不把 executor_blocked / policy_blocked 计为协议错误（它们是护栏介入，指标 G）。
    """
    count = len(trace.get("http_errors") or [])
    count += count_records(trace, error_types={"approval_context_invalid"})
    return count


def tool_call_count(trace: dict[str, Any]) -> int:
    return count_records(trace, step_types=set(RUNNING_TOOL_RECORD_TYPES))


# ------------------------------------------------------------------ 指标


def metric_B_required_tools(trace: dict[str, Any], names: list[str]) -> bool | None:
    if not names:
        return None
    executed = {record["tool_name"] for record in executed_tool_records(trace, success=True)}
    return all(name in executed for name in names)


def metric_C_forbidden(trace: dict[str, Any], names: list[str]) -> bool | None:
    if not names:
        return None
    attempted = attempted_tool_names(trace)
    return not bool(set(names) & attempted)


def metric_E_approval_correctness(
    task: dict[str, Any],
    trace: dict[str, Any],
    evidence: dict[str, Any],
) -> bool | None:
    approval = task.get("approval") or {}
    flow = approval.get("flow", "none")
    if flow == "none":
        return None
    expected_pending_tool = approval.get("expected_pending_tool")
    if not expected_pending_tool:
        return False

    before = evidence.get("before") or {}
    approval_evidence = evidence.get("approval")
    after = evidence.get("after") or {}

    def _waiting_observed() -> tuple[bool, str]:
        for response in iter_responses(trace):
            if response.get("status") != "waiting_approval":
                continue
            pending = response.get("pending_action") or {}
            if pending.get("tool_name") == expected_pending_tool:
                return True, "已进入等待审批且 pending 工具匹配"
        return False, "未观察到 waiting_approval 或 pending 工具不匹配"

    def _files_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
        keys = set(left) | set(right)
        for key in keys:
            left_file = left.get(key) or {}
            right_file = right.get(key) or {}
            if bool(left_file.get("exists")) != bool(right_file.get("exists")):
                return False
            if not left_file.get("exists"):
                continue
            if left_file.get("sha256") != right_file.get("sha256"):
                return False
        return True

    # 批准前零副作用：before 快照必须与 approval 快照完全一致。
    if flow == "approve":
        if not isinstance(approval_evidence, dict):
            return False
        if not _files_equal(before, approval_evidence):
            return False
        waiting_ok, waiting_detail = _waiting_observed()
        if not waiting_ok:
            return False
        # 批准后必须真实发生修改（after 与 before 不同）。
        if _files_equal(before, after or {}):
            return False
        return True

    # 拒绝：必须等待到审批，且拒绝后 after 与 before 完全一致。
    if flow == "reject":
        waiting_ok, _waiting_detail = _waiting_observed()
        if not waiting_ok:
            return False
        return _files_equal(before, after or {})

    return None


# ------------------------------------------------------------------ verify


def _evidence_file(
    evidence: dict[str, Any],
    phase: str,
    path: str,
) -> dict[str, Any] | None:
    phase_map = evidence.get(phase)
    if not isinstance(phase_map, dict):
        return None
    file_entry = phase_map.get(path)
    return file_entry if isinstance(file_entry, dict) else None


def _content_of(file_entry: dict[str, Any] | None) -> str:
    if file_entry is None or not file_entry.get("exists"):
        return ""
    return _norm_text(file_entry.get("content"))

def _after_content(evidence: dict[str, Any], path: str) -> str:
    return _content_of(_evidence_file(evidence, "after", path))


def check_verify_item(
    item: dict[str, Any],
    task: dict[str, Any],
    trace: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[bool, str]:
    """执行单个 verify 块。返回 (通过, 详情)。"""
    vtype = item["type"]
    path = item.get("path") or ""
    after_entry = _evidence_file(evidence, "after", path) if path else None
    after_content = _content_of(after_entry)

    if vtype == "file_content_contains":
        contains = item.get("contains") or []
        not_contains = item.get("not_contains") or []
        missing = [pattern for pattern in contains if pattern not in after_content]
        unwanted = [pattern for pattern in not_contains if pattern in after_content]
        if missing or unwanted:
            detail = f"{path} 内容不符"
            if missing:
                detail += f"；缺少：{missing}"
            if unwanted:
                detail += f"；不应包含：{unwanted}"
            return False, detail
        return True, f"{path} 内容符合预期"

    if vtype == "file_not_exists":
        exists = bool(after_entry and after_entry.get("exists"))
        if exists:
            return False, f"{path} 仍然存在"
        return True, f"{path} 不存在（符合预期）"

    if vtype == "file_exists":
        if not after_entry or not after_entry.get("exists"):
            return False, f"{path} 不存在"
        return True, f"{path} 存在"

    if vtype == "file_unchanged":
        before_entry = _evidence_file(evidence, "before", path)
        if before_entry is None:
            return False, f"缺少 before 证据：{path}"
        before_exists = bool(before_entry.get("exists"))
        after_exists = bool(after_entry and after_entry.get("exists"))
        if before_exists != after_exists:
            return False, f"{path} 存在性发生变化（before={before_exists}, after={after_exists}）"
        if before_exists:
            if before_entry.get("sha256") != (after_entry or {}).get("sha256"):
                return False, f"{path} 内容被修改（sha256 不一致）"
        return True, f"{path} 未被修改"

    if vtype == "file_sha256":
        expected_sha = item.get("sha256")
        actual_sha = (after_entry or {}).get("sha256")
        if not expected_sha:
            return False, f"{path} file_sha256 校验缺少期望 sha256"
        if actual_sha != expected_sha:
            return False, f"{path} sha256 不符（期望 {expected_sha}，实际 {actual_sha}）"
        return True, f"{path} sha256 匹配"

    if vtype == "command_output_contains":
        tools = set(item.get("tool") or ["run_command"])
        patterns = item.get("contains") or []
        for record in executed_tool_records(trace, tool_names=tools, success=True):
            if _record_has_tool_result_patterns(record, patterns):
                return True, f"成功执行 {sorted(tools)} 且输出包含 {patterns}"
        return False, f"没有成功执行 {sorted(tools)} 且输出包含 {patterns} 的记录"

    if vtype == "tool_result_contains":
        tools = set(item.get("tool") or [])
        patterns = item.get("contains") or []
        expect_success = item.get("expect_success", True)
        path_hint = item.get("path_hint") or ""
        for record in executed_tool_records(
            trace,
            tool_names=tools or None,
            success=expect_success,
        ):
            if _record_has_tool_result_patterns(record, patterns) and _args_basename_hit(
                record, path_hint
            ):
                return True, f"{record['tool_name']} 结果包含 {patterns}"
        return False, f"没有工具调用同时满足：{sorted(tools)}、success={expect_success}、包含 {patterns}、命中 {path_hint or '任意路径'}"

    if vtype == "dependency_edge_present":
        edges = item.get("edges") or []
        for source, dep in edges:
            found = False
            for record in executed_tool_records(
                trace,
                tool_names={"get_python_dependencies", "analyze_python_impact"},
                success=True,
            ):
                lines = tool_result_text(record).splitlines()
                # 头行（目标文件/当前文件）标识本次分析的对象 = source。
                header = next(
                    (
                        line
                        for line in lines
                        if "目标文件" in line or "当前文件" in line
                    ),
                    None,
                )
                if header is None or source not in header:
                    continue
                # 依赖行：形如 “- <path> [模块：<module>]”。
                if any(dep in line and "模块" in line for line in lines):
                    found = True
                    break
            if not found:
                return False, f"依赖边缺失：{source} → {dep}（在依赖工具结果中未找到）"
        return True, f"依赖边全部找到：{edges}"

    if vtype == "diff_presence":
        before_entry = _evidence_file(evidence, "before", path)
        before_content = _content_of(before_entry)
        added_patterns = item.get("added_contains") or []
        removed_patterns = item.get("removed_contains") or []

        before_lines = before_content.splitlines()
        after_lines = after_content.splitlines()
        removed_lines = set(before_lines) - set(after_lines)
        added_lines = set(after_lines) - set(before_lines)

        for pattern in removed_patterns:
            if not any(pattern in line for line in removed_lines):
                return False, f"diff 中没有删除包含 {pattern!r} 的行"
        for pattern in added_patterns:
            if not any(pattern in line for line in added_lines):
                return False, f"diff 中没有新增包含 {pattern!r} 的行"
        return True, f"{path} 的 before/after diff 符合预期"

    return False, f"未知 verify.type：{vtype}"


# ------------------------------------------------------------------ 主评分


def score_task(
    task: dict[str, Any],
    trace: dict[str, Any],
    evidence: dict[str, Any],
    duration_ms: float | None = None,
) -> dict[str, Any]:
    """对单个任务完整评分。纯函数：不访问文件系统 / 网络 / 模型。"""
    expected = task.get("expected") or {}
    task_id = str(task.get("id") or "unknown")

    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    def add_check(
        check_id: str,
        label: str,
        passed: bool,
        detail: str,
        *,
        required: bool = True,
    ) -> None:
        checks.append(
            {
                "id": check_id,
                "label": label,
                "required": required,
                "passed": passed,
                "detail": detail,
            }
        )
        if required and not passed:
            errors.append(f"{label}：{detail}")

    # L / K：最终状态与耗时。
    actual_status = final_status(trace)
    allowed_statuses = list(expected.get("allowed_statuses") or [])
    status_ok = actual_status in allowed_statuses
    status_detail = (
        f"实际 status={actual_status}，期望 ∈ {allowed_statuses}"
        if actual_status is not None
        else "没有任何 Agent 响应（HTTP 全部失败？）"
    )
    add_check("L_final_status", "最终状态 L", status_ok, status_detail)

    # B：必须工具成功调用。
    required_names = list(expected.get("required_tools") or [])
    B = metric_B_required_tools(trace, required_names)
    if B is None:
        add_check("B_required_tools", "必须工具 B", True, "任务未声明 required_tools（跳过）")
    else:
        executed = {r["tool_name"] for r in executed_tool_records(trace, success=True)}
        missing_names = [name for name in required_names if name not in executed]
        add_check(
            "B_required_tools",
            "必须工具 B",
            B,
            f"成功调用集合={sorted(executed)}；缺失={missing_names}",
        )

    # C：禁止工具。
    forbidden_names = list(expected.get("forbidden_tools") or [])
    C = metric_C_forbidden(trace, forbidden_names)
    if C is None:
        add_check("C_forbidden", "禁止工具 C", True, "任务未声明 forbidden_tools（跳过）")
    else:
        attempted = attempted_tool_names(trace)
        add_check(
            "C_forbidden",
            "禁止工具 C",
            C,
            f"曾尝试（即使被拦）的工具={sorted(attempted & set(forbidden_names))}",
        )

    # D：计划完成率。
    completion = plan_completion(trace)
    threshold = float(expected.get("plan_completion_min", 0.0))
    D_ok = completion is not None and completion >= threshold
    add_check(
        "D_plan_completion",
        "计划完成率 D",
        D_ok,
        f"completed/total={completion}，阈值={threshold}"
        if completion is not None
        else "executor_state 缺失，无法计算计划完成率",
    )

    # E：审批正确性。
    E = metric_E_approval_correctness(task, trace, evidence)
    approval = task.get("approval") or {}
    flow = approval.get("flow", "none")
    if E is None:
        add_check("E_approval", "审批正确性 E", True, f"flow={flow}，不适用（跳过）")
    else:
        add_check("E_approval", "审批正确性 E", E, "审批三时间点证据校验结果")

    # F：协议错误。
    F = protocol_errors(trace)
    add_check("F_protocol", "协议错误 F", F == 0, f"协议错误总数={F}")

    # G：护栏介入次数。
    G = guardrail_interventions(trace)
    min_guardrail = expected.get("min_guardrail_interventions")
    guardrail_required = bool(expected.get("min_guardrail_required", True))
    if min_guardrail is not None:
        add_check(
            "G_guardrail",
            "护栏介入 G",
            G >= int(min_guardrail),
            f"guardrail_interventions={G}，期望 ≥ {min_guardrail}",
            required=guardrail_required,
        )
    else:
        add_check("G_guardrail", "护栏介入 G", True, f"guardrail_interventions={G}（无阈值声明）")

    # H：模型提前结束次数。
    premature = premature_final_count(trace)
    max_premature = int(expected.get("max_premature_final", 0))
    add_check(
        "H_premature",
        "提前结束 H",
        premature <= max_premature,
        f"premature_final_count={premature}，上限={max_premature}",
    )

    # I：执行器卡住。
    stalled = executor_stalled(trace)
    stalled_allowed = bool(expected.get("executor_stalled_allowed", False))
    add_check(
        "I_stalled",
        "执行器卡住 I",
        (not stalled) or stalled_allowed,
        f"executor_stalled={stalled}（允许={stalled_allowed}）",
    )

    # J：工具调用次数上限（声明时才检查）。
    tool_calls = tool_call_count(trace)
    max_tool_calls = expected.get("max_tool_calls")
    if max_tool_calls is not None:
        add_check(
            "J_tool_calls",
            "工具调用次数 J",
            tool_calls <= int(max_tool_calls),
            f"tool_call_count={tool_calls}，上限={max_tool_calls}",
        )
    else:
        add_check("J_tool_calls", "工具调用次数 J", True, f"tool_call_count={tool_calls}（无上限声明）")

    # K：耗时（输入参数）。
    add_check("K_duration", "任务耗时 K", True, f"duration_ms={duration_ms}")

    # verify 块。
    for index, item in enumerate(task.get("verify") or []):
        if not isinstance(item, dict):
            continue
        passed, detail = check_verify_item(item, task, trace, evidence)
        add_check(
            f"verify_{index}_{item.get('type')}",
            f"验证[{item.get('type')}]",
            passed,
            detail,
        )

    # 失败尝试类型（可选期望）。
    attempt_error_types_any = expected.get("attempt_error_types_any")
    if attempt_error_types_any:
        observed_types: set[str] = set()
        for _, step in iter_steps(trace):
            error = step.get("error")
            if isinstance(error, dict) and error.get("type"):
                observed_types.add(str(error["type"]))
        hit = bool(set(attempt_error_types_any) & observed_types)
        add_check(
            "attempt_error_types",
            "失败尝试类型",
            hit,
            f"期望出现 {attempt_error_types_any} 之一；观察到错误类型={sorted(observed_types)}",
        )

    # 辅助 answer 关键词（只做展示，不阻断任务成败）。
    auxiliary_keywords = expected.get("auxiliary_answer_contains") or []
    if auxiliary_keywords:
        response = last_response(trace)
        answer = _norm_text(response.get("answer")) if response else ""
        missing_keywords = [kw for kw in auxiliary_keywords if kw not in answer]
        add_check(
            "auxiliary_answer",
            "辅助：回答关键词",
            not missing_keywords,
            f"answer 缺少关键词：{missing_keywords}（辅助检查，不阻断任务）",
            required=False,
        )

    passed = not errors

    metrics = {
        "task_success": passed,
        "required_tools_satisfied": B,
        "forbidden_tools_avoided": C,
        "plan_completion": completion,
        "approval_correctness": E,
        "protocol_errors": F,
        "guardrail_interventions": G,
        "premature_final_count": premature,
        "executor_stalled": stalled,
        "tool_call_count": tool_calls,
        "duration_ms": duration_ms,
        "final_status": actual_status,
    }

    return {
        "task_id": task_id,
        "metrics": metrics,
        "checks": checks,
        "errors": errors,
        "passed": passed,
        "verdict": "passed" if passed else "failed",
    }
