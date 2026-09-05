"""Day19 Agent Eval：评分器纯函数测试。

全部使用合成 task / trace / evidence，不发送 HTTP、不访问真实 workspace。

覆盖场景：
- A/L/K：最终状态、耗时、整体通过；
- B：required_tools 成功调用存在性（缺失 / success=False 均失败）；
- C：forbidden_tools 即使被拦也算尝试（executor_blocked / policy_blocked）；
- D：plan_completion 阈值（executor_state 缺失 → 失败）；
- E：审批三时间点证据（approve 零副作用+真实修改 / reject 无副作用；
      approval 快照与 before 不一致 → 失败）；
- F：protocol_errors（http_errors / approval_context_invalid 计数，
      executor_blocked 不计入）；
- G：guardrail_interventions 计数与 min 阈值（含 required=false）；
- H：premature_final_count 上限；
- I：executor_stalled（顶层 error）与允许位；
- J：max_tool_calls 上限；
- 9 种 verify 类型逐一命中与失败路径；
- 未知 verify 类型报错；纯函数确定性（同输入同输出）；畸形输入不抛异常。
"""

import hashlib
from pathlib import Path

from evals import scoring
from evals.scoring import (
    check_verify_item,
    final_status,
    guardrail_interventions,
    iter_steps,
    plan_completion,
    premature_final_count,
    protocol_errors,
    score_task,
    tool_call_count,
)


# ---------------------------------------------------------------- 构造工具


def step(
    tool_name,
    *,
    success=True,
    tool_result="",
    error=None,
    step_type="tool_call",
    tool_args=None,
):
    item = {
        "type": step_type,
        "tool_name": tool_name,
        "tool_args": tool_args if tool_args is not None else {},
        "success": success,
        "tool_result": tool_result,
    }
    if error is not None:
        item["error"] = error
    return item


def response(status="finished", *, steps=None, executor_state=None, answer=""):
    item = {"status": status, "answer": answer, "thread_id": "thread_1"}
    if steps is not None:
        item["steps"] = steps
    if executor_state is not None:
        item["executor_state"] = executor_state
    return item


def exec_state(total=1, completed=1):
    return {
        "status": "running",
        "total_steps": total,
        "completed_steps": completed,
        "blocked_calls": 0,
        "supporting_calls": 0,
        "warning_steps": 0,
        "current_step_position": completed,
        "completion_status": "completed" if completed >= total else "in_progress",
    }


def trace(*, invoke=None, resumes=None, http_errors=None, notes=None):
    return {
        "invoke": invoke,
        "resumes": list(resumes or []),
        "http_errors": list(http_errors or []),
        "notes": list(notes or []),
    }


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_entry(text, *, exists=True):
    return {
        "exists": exists,
        "sha256": sha256(text) if exists else None,
        "content": text if exists else None,
    }


def evidence(before=None, approval=None, after=None, events=None):
    return {
        "before": before or {},
        "approval": approval or {},
        "after": after or {},
        "approval_events": list(events or []),
    }


def base_task(**overrides):
    task = {
        "id": "t_synth",
        "name": "合成任务",
        "category": "read",
        "max_steps": 8,
        "prompt_template": "{case_root}",
        "expected": {
            "allowed_statuses": ["finished"],
            "required_tools": ["read_file_lines"],
            "forbidden_tools": ["edit_file"],
            "plan_completion_min": 1.0,
            "max_premature_final": 0,
            "executor_stalled_allowed": False,
        },
        "approval": {"flow": "none", "expected_pending_tool": None},
        "verify": [],
    }
    task.update(overrides)
    return task


def trivial_trace(*, http_errors=None):
    """一个 read 任务成功完成的 trace。"""
    return trace(
        invoke=response(
            "finished",
            steps=[
                step("read_file_lines", tool_result="def multiply(a, b): ..."),
            ],
            executor_state=exec_state(),
        ),
        http_errors=http_errors,
    )


def trivial_task(**overrides):
    return base_task(**overrides)


def _failed_ids(result):
    return {
        check["id"]
        for check in result["checks"]
        if check["required"] and not check["passed"]
    }


# ---------------------------------------------------------------- A/B/C/D/L/K


def test_trivial_read_task_passes():
    result = score_task(
        trivial_task(),
        trivial_trace(),
        evidence(after={"math_utils.py": file_entry("def multiply(a, b): ...")}),
        duration_ms=1234.5,
    )
    assert result["passed"] is True
    assert result["verdict"] == "passed"
    metrics = result["metrics"]
    assert metrics["task_success"] is True
    assert metrics["required_tools_satisfied"] is True
    assert metrics["forbidden_tools_avoided"] is True
    assert metrics["plan_completion"] == 1.0
    assert metrics["protocol_errors"] == 0
    assert metrics["tool_call_count"] == 1
    assert metrics["duration_ms"] == 1234.5
    assert metrics["final_status"] == "finished"


def test_missing_required_tool_fails_B():
    result = score_task(
        trivial_task(),
        trace(invoke=response("finished", executor_state=exec_state())),
        evidence(),
    )
    assert result["passed"] is False
    assert "B_required_tools" in _failed_ids(result)
    assert any("缺失" in check["detail"] for check in result["checks"] if check["id"] == "B_required_tools")


def test_required_tool_failed_call_fails_B():
    result = score_task(
        trivial_task(),
        trace(
            invoke=response(
                "finished",
                steps=[step("read_file_lines", success=False, tool_result="")],
                executor_state=exec_state(),
            )
        ),
        evidence(),
    )
    assert result["passed"] is False
    assert "B_required_tools" in _failed_ids(result)


def test_forbidden_tool_attempted_but_blocked_still_fails_C():
    """即使被 executor_blocked 拦截，尝试过 edit_file 也算违规。"""
    result = score_task(
        trivial_task(),
        trace(
            invoke=response(
                "finished",
                steps=[
                    step(
                        "edit_file",
                        success=False,
                        error={"type": "plan_step_violation", "message": "越序"},
                        step_type="executor_blocked",
                    ),
                    step("read_file_lines", tool_result="ok"),
                ],
                executor_state=exec_state(),
            )
        ),
        evidence(),
    )
    assert result["passed"] is False
    assert "C_forbidden" in _failed_ids(result)


def test_policy_blocked_also_counts_as_attempt():
    """edit_file 被 policy 拦截（policy_blocked）也算尝试过 → C 失败。"""
    result = score_task(
        trivial_task(),
        trace(
            invoke=response(
                "finished",
                steps=[
                    step(
                        "edit_file",
                        success=False,
                        error={"type": "policy_violation", "message": "需审批"},
                        step_type="policy_blocked",
                    ),
                    step("read_file_lines", tool_result="ok"),
                ],
                executor_state=exec_state(),
            )
        ),
        evidence(),
    )
    assert result["passed"] is False
    assert "C_forbidden" in _failed_ids(result)


def test_forbidden_not_declared_skipped_C():
    task = trivial_task()
    task["expected"] = dict(task["expected"], forbidden_tools=[])
    result = score_task(task, trivial_trace(), evidence())
    assert result["passed"] is True


def test_plan_completion_below_threshold_fails_D():
    task = trivial_task()
    result = score_task(
        task,
        trace(
            invoke=response(
                "finished",
                steps=[step("read_file_lines", tool_result="ok")],
                executor_state=exec_state(total=4, completed=2),
            )
        ),
        evidence(),
    )
    assert result["passed"] is False
    assert "D_plan_completion" in _failed_ids(result)
    assert result["metrics"]["plan_completion"] == 0.5


def test_missing_executor_state_fails_D():
    result = score_task(
        trivial_task(),
        trace(invoke=response("finished", steps=[step("read_file_lines", tool_result="ok")])),
        evidence(),
    )
    assert result["passed"] is False
    assert "D_plan_completion" in _failed_ids(result)
    assert result["metrics"]["plan_completion"] is None


def test_status_not_allowed_fails_L():
    task = trivial_task()
    task["expected"] = dict(task["expected"], allowed_statuses=["max_steps_reached"])
    result = score_task(
        task,
        trace(
            invoke=response(
                "finished",
                steps=[step("read_file_lines", tool_result="ok")],
                executor_state=exec_state(),
            )
        ),
        evidence(),
    )
    assert result["passed"] is False
    assert "L_final_status" in _failed_ids(result)


def test_no_response_at_all_fails_L():
    result = score_task(
        trivial_task(),
        trace(invoke=None, http_errors=[{"phase": "invoke", "attempt": 1, "message": "连接失败"}]),
        evidence(),
    )
    assert result["passed"] is False
    assert result["metrics"]["final_status"] is None
    assert any("没有任何 Agent 响应" in check["detail"] for check in result["checks"])


def test_max_tool_calls_exceeded_fails_J():
    task = trivial_task()
    task["expected"] = dict(task["expected"], max_tool_calls=2)
    result = score_task(
        task,
        trace(
            invoke=response(
                "finished",
                steps=[
                    step("read_file_lines", tool_result="a"),
                    step("read_file_lines", tool_result="b"),
                    step("read_file_lines", tool_result="c"),
                ],
                executor_state=exec_state(),
            )
        ),
        evidence(),
    )
    assert result["passed"] is False
    assert "J_tool_calls" in _failed_ids(result)
    assert result["metrics"]["tool_call_count"] == 3


# ---------------------------------------------------------------- E 审批


def _approval_task(flow):
    return base_task(
        approval={"flow": flow, "expected_pending_tool": "edit_file"},
        expected={
            "allowed_statuses": ["finished"] if flow == "approve" else ["rejected"],
            "required_tools": ["edit_file"] if flow == "approve" else [],
            "forbidden_tools": [],
            "plan_completion_min": 1.0 if flow == "approve" else 0.0,
            "max_premature_final": 0,
            "executor_stalled_allowed": False,
        },
    )


def test_approve_flow_correct_E():
    before = {"math_utils.py": file_entry("返回 a 和 b 的乘积")}
    after = {"math_utils.py": file_entry("返回两个数的乘积")}
    t = trace(
        invoke=response(
            "waiting_approval",
            steps=None,
            executor_state=exec_state(total=1, completed=0),
        ),
        resumes=[
            response(
                "finished",
                steps=[
                    step("read_file_lines", tool_result="doc"),
                    step("edit_file", success=True, tool_args={"path": "math_utils.py"}),
                ],
                executor_state=exec_state(total=2, completed=2),
            )
        ],
    )
    # pending_action 需要出现在 interrupt 响应的顶层：
    t["invoke"]["pending_action"] = {"tool_name": "edit_file", "tool_args": {}}
    result = score_task(
        _approval_task("approve"),
        t,
        evidence(before=before, approval=before, after=after),
    )
    assert result["metrics"]["approval_correctness"] is True
    assert result["passed"] is True


def test_approve_flow_side_effect_before_approval_fails_E():
    """approval 快照与 before 不一致（审批前已产生副作用）→ E 失败。"""
    before = {"math_utils.py": file_entry("返回 a 和 b 的乘积")}
    approval = {"math_utils.py": file_entry("返回两个数的乘积")}
    after = {"math_utils.py": file_entry("返回两个数的乘积")}
    t = trace(
        invoke=response("waiting_approval", executor_state=exec_state(total=1, completed=0)),
        resumes=[response("finished", steps=[step("edit_file")], executor_state=exec_state())],
    )
    t["invoke"]["pending_action"] = {"tool_name": "edit_file"}
    result = score_task(_approval_task("approve"), t, evidence(before=before, approval=approval, after=after))
    assert result["metrics"]["approval_correctness"] is False
    assert "E_approval" in _failed_ids(result)


def test_reject_flow_no_side_effect_E():
    before = {"math_utils.py": file_entry("返回 a 和 b 的乘积")}
    t = trace(
        invoke=response(
            "waiting_approval",
            executor_state=exec_state(total=1, completed=0),
        ),
        resumes=[
            response(
                "rejected",
                steps=[step("edit_file", success=False, error={"type": "approval_rejected"})],
                executor_state=exec_state(total=1, completed=0),
            )
        ],
    )
    t["invoke"]["pending_action"] = {"tool_name": "edit_file"}
    result = score_task(_approval_task("reject"), t, evidence(before=before, approval=before, after=before))
    assert result["metrics"]["approval_correctness"] is True
    assert result["passed"] is True


def test_reject_flow_with_modification_fails_E():
    """拒绝后文件仍被修改（after != before）→ E 失败。"""
    before = {"math_utils.py": file_entry("返回 a 和 b 的乘积")}
    after = {"math_utils.py": file_entry("返回两个数的乘积")}
    t = trace(
        invoke=response("waiting_approval", executor_state=exec_state()),
        resumes=[response("rejected", executor_state=exec_state(total=1, completed=0))],
    )
    t["invoke"]["pending_action"] = {"tool_name": "edit_file"}
    result = score_task(_approval_task("reject"), t, evidence(before=before, approval=before, after=after))
    assert result["metrics"]["approval_correctness"] is False
    assert "E_approval" in _failed_ids(result)


def test_approval_not_waiting_fails_E():
    """flow=approve 但从未进入 waiting_approval → E 失败。"""
    before = {"math_utils.py": file_entry("返回 a 和 b 的乘积")}
    t = trace(
        invoke=response(
            "finished",
            steps=[step("edit_file", success=True)],
            executor_state=exec_state(),
        )
    )
    result = score_task(
        _approval_task("approve"),
        t,
        evidence(before=before, approval=before, after=before),
    )
    assert result["metrics"]["approval_correctness"] is False


# ---------------------------------------------------------------- F/G/H/I


def test_protocol_errors_count_http_and_context_errors_F():
    """F 统计 http_errors + approval_context_invalid，不含 executor_blocked。"""
    t = trace(
        invoke=response(
            "finished",
            steps=[
                step(
                    "read_file_lines",
                    success=False,
                    error={"type": "approval_context_invalid"},
                ),
                step(
                    "edit_file",
                    success=False,
                    error={"type": "plan_step_violation"},
                    step_type="executor_blocked",
                ),
                step("read_file_lines", tool_result="ok"),
            ],
            executor_state=exec_state(),
        ),
        http_errors=[{"phase": "resume", "attempt": 1, "status_code": 400}, {"phase": "invoke", "attempt": 1}],
    )
    assert protocol_errors(t) == 3
    result = score_task(trivial_task(), t, evidence())
    assert result["passed"] is False
    assert "F_protocol" in _failed_ids(result)


def test_guardrail_interventions_count_G():
    # 用非 forbidden 工具触发护栏（run_command 越序 / write_new_file 违反策略），
    # 避免与 C（禁止工具）互相干扰。
    t = trace(
        invoke=response(
            "finished",
            steps=[
                step("run_command", success=False, error={"type": "plan_step_violation"}, step_type="executor_blocked"),
                step("write_new_file", success=False, error={"type": "policy_violation"}, step_type="policy_blocked"),
                step("read_file_lines", tool_result="ok"),
            ],
            executor_state=exec_state(),
        )
    )
    assert guardrail_interventions(t) == 2
    # 默认任务不要求护栏；G 检查不阻断。
    result = score_task(trivial_task(), t, evidence())
    assert result["passed"] is True
    assert result["metrics"]["guardrail_interventions"] == 2


def test_min_guardrail_threshold_and_required_flag():
    task = trivial_task()
    task["expected"] = dict(
        task["expected"],
        min_guardrail_interventions=1,
        min_guardrail_required=False,
    )
    no_guardrail_trace = trivial_trace()
    result = score_task(task, no_guardrail_trace, evidence())
    # required=false：不满足也只展示不失败。
    assert result["passed"] is True
    assert any(
        check["id"] == "G_guardrail" and not check["passed"] for check in result["checks"]
    )

    task["expected"]["min_guardrail_required"] = True
    result = score_task(task, no_guardrail_trace, evidence())
    assert result["passed"] is False
    assert "G_guardrail" in _failed_ids(result)


def test_premature_final_count_H():
    t = trace(
        invoke=response(
            "finished",
            steps=[
                step("read_file_lines", success=False, error={"type": "premature_final_answer"}),
                step("read_file_lines", success=False, error={"type": "premature_final_answer"}),
                step("read_file_lines", tool_result="ok"),
            ],
            executor_state=exec_state(),
        )
    )
    assert premature_final_count(t) == 2
    result = score_task(trivial_task(), t, evidence())
    assert result["passed"] is False
    assert "H_premature" in _failed_ids(result)

    task = trivial_task()
    task["expected"] = dict(task["expected"], max_premature_final=2)
    result = score_task(task, t, evidence())
    assert result["passed"] is True


def test_executor_stalled_I():
    t = trace(
        invoke=response(
            "failed",
            steps=[step("read_file_lines", success=False, error={"type": "premature_final_answer"})],
            executor_state=exec_state(total=2, completed=1),
            answer="",
        ),
    )
    # 顶层 error 需要显式出现：
    t["invoke"]["error"] = {"type": "executor_stalled", "message": "两次提前结束"}
    result = score_task(trivial_task(), t, evidence())
    assert result["metrics"]["executor_stalled"] is True
    assert result["passed"] is False
    assert "I_stalled" in _failed_ids(result)

    # 允许卡住 + 放宽提前结束上限 + 允许 failed 终态 → 通过。
    task = trivial_task()
    task["expected"] = dict(
        task["expected"],
        executor_stalled_allowed=True,
        max_premature_final=1,
        required_tools=[],
        plan_completion_min=0.0,
        allowed_statuses=["finished", "failed"],
    )
    result = score_task(task, t, evidence())
    assert result["passed"] is True


def test_executor_stalled_absent_ok():
    result = score_task(trivial_task(), trivial_trace(), evidence())
    assert result["metrics"]["executor_stalled"] is False


# ---------------------------------------------------------------- 工具函数信号


def test_iter_steps_orders_invoke_then_resumes():
    t = trace(
        invoke=response("finished", steps=[step("read_file_lines", tool_result="a")]),
        resumes=[response("finished", steps=[step("edit_file", tool_result="b")])],
    )
    ordered = list(iter_steps(t))
    assert [record["tool_name"] for _, record in ordered] == ["read_file_lines", "edit_file"]


def test_final_status_takes_last_resume_when_present():
    t = trace(
        invoke=response("waiting_approval"),
        resumes=[response("rejected")],
    )
    assert final_status(t) == "rejected"


def test_plan_completion_uses_last_response_executor_state():
    t = trace(
        invoke=response("waiting_approval", executor_state=exec_state(total=1, completed=0)),
        resumes=[response("finished", executor_state=exec_state(total=3, completed=3))],
    )
    assert plan_completion(t) == 1.0


def test_tool_call_count_counts_executed_only():
    t = trace(
        invoke=response(
            "finished",
            steps=[
                step("read_file_lines", tool_result="ok"),
                step("edit_file", success=False, error={"type": "plan_step_violation"}, step_type="executor_blocked"),
                step("write_new_file", success=False, error={"type": "policy_violation"}, step_type="policy_blocked"),
            ],
            executor_state=exec_state(total=3, completed=3),
        )
    )
    assert tool_call_count(t) == 1


# ---------------------------------------------------------------- verify 类型


def _verify_task(items, task=None):
    """verify-only 任务：清除 B/C 声明，避免工具集合干扰 verify 判定。"""
    if task is None:
        task = trivial_task()
    task["verify"] = items
    task["expected"] = dict(
        task["expected"],
        required_tools=[],
        forbidden_tools=[],
    )
    return task


def test_verify_file_content_contains_and_not_contains():
    after = {"f.py": file_entry("line1\n内容A\nline3")}
    result = score_task(
        _verify_task([
            {"type": "file_content_contains", "path": "f.py", "contains": ["内容A"], "not_contains": ["内容B"]},
        ]),
        trivial_trace(),
        evidence(after=after),
    )
    assert result["passed"] is True

    result = score_task(
        _verify_task([
            {"type": "file_content_contains", "path": "f.py", "contains": ["内容B"], "not_contains": ["内容A"]},
        ]),
        trivial_trace(),
        evidence(after=after),
    )
    assert result["passed"] is False
    assert result["errors"][0].startswith("验证[file_content_contains]")


def test_verify_file_exists_and_not_exists():
    after = {"f.py": file_entry("x")}
    assert score_task(_verify_task([{"type": "file_exists", "path": "f.py"}]), trivial_trace(), evidence(after=after))["passed"]
    assert not score_task(_verify_task([{"type": "file_not_exists", "path": "f.py"}]), trivial_trace(), evidence(after=after))["passed"]
    assert score_task(_verify_task([{"type": "file_not_exists", "path": "gone.py"}]), trivial_trace(), evidence(after=after))["passed"]
    assert not score_task(_verify_task([{"type": "file_exists", "path": "gone.py"}]), trivial_trace(), evidence(after=after))["passed"]


def test_verify_file_unchanged():
    before = {"f.py": file_entry("same")}
    after_same = {"f.py": file_entry("same")}
    after_changed = {"f.py": file_entry("different")}
    result = score_task(
        _verify_task([{"type": "file_unchanged", "path": "f.py"}]),
        trivial_trace(),
        evidence(before=before, after=after_same),
    )
    assert result["passed"] is True
    result = score_task(
        _verify_task([{"type": "file_unchanged", "path": "f.py"}]),
        trivial_trace(),
        evidence(before=before, after=after_changed),
    )
    assert result["passed"] is False


def test_verify_file_sha256():
    text = "def f():\n    return 1\n"
    after = {"f.py": file_entry(text)}
    ok = score_task(
        _verify_task([{"type": "file_sha256", "path": "f.py", "sha256": sha256(text)}]),
        trivial_trace(),
        evidence(after=after),
    )
    assert ok["passed"] is True
    bad = score_task(
        _verify_task([{"type": "file_sha256", "path": "f.py", "sha256": "0" * 64}]),
        trivial_trace(),
        evidence(after=after),
    )
    assert bad["passed"] is False


def test_verify_command_output_contains():
    run_step = step(
        "run_command",
        tool_result="执行成功，退出码 returncode：0",
        tool_args={"command": "python -m pytest tests/ -q", "cwd": "x"},
    )
    t = trace(
        invoke=response("finished", steps=[run_step], executor_state=exec_state())
    )
    result = score_task(
        _verify_task([
            {"type": "command_output_contains", "tool": ["run_command"], "contains": ["returncode：0"]},
        ]),
        t,
        evidence(),
    )
    assert result["passed"] is True

    failed_run = step(
        "run_command",
        success=False,
        tool_result="退出码 returncode：1",
        error={"type": "command_failed"},
    )
    t2 = trace(invoke=response("finished", steps=[failed_run], executor_state=exec_state()))
    result = score_task(
        _verify_task([
            {"type": "command_output_contains", "tool": ["run_command"], "contains": ["returncode：0"]},
        ]),
        t2,
        evidence(),
    )
    assert result["passed"] is False


def test_verify_tool_result_contains_with_path_hint():
    outline = "文件：workspace/eval_cases/run/t/demo_project/math_utils.py\n- [函数] multiply （第 1-3 行）"
    record = step("get_python_file_outline", tool_result=outline,
                  tool_args={"path": "workspace/eval_cases/run/t/demo_project/math_utils.py"})
    t = trace(invoke=response("finished", steps=[record], executor_state=exec_state()))

    task = _verify_task([
        {"type": "tool_result_contains", "tool": ["get_python_file_outline"],
         "path_hint": "math_utils.py", "expect_success": True,
         "contains": ["[函数] multiply"]},
    ])
    assert score_task(task, t, evidence())["passed"] is True

    # path_hint 指向别的文件 → 不匹配。
    # 注意：不能用 "utils.py" 作反例——"math_utils.py" 内含该子串。
    task2 = _verify_task([
        {"type": "tool_result_contains", "tool": ["get_python_file_outline"],
         "path_hint": "main.py", "expect_success": True,
         "contains": ["[函数] multiply"]},
    ])
    assert score_task(task2, t, evidence())["passed"] is False


def test_verify_dependency_edge_present():
    deps_api = (
        "目标文件：workspace/eval_cases/r/t/demo_project/api.py\n"
        "依赖：\n"
        "- workspace/eval_cases/r/t/demo_project/service.py [模块：service]\n"
    )
    deps_service = (
        "目标文件：workspace/eval_cases/r/t/demo_project/service.py\n"
        "依赖：\n"
        "- workspace/eval_cases/r/t/demo_project/models.py [模块：models]\n"
    )
    t = trace(
        invoke=response(
            "finished",
            steps=[
                step("get_python_dependencies", tool_result=deps_api),
                step("get_python_dependencies", tool_result=deps_service),
            ],
            executor_state=exec_state(total=2, completed=2),
        )
    )
    result = score_task(
        _verify_task([
            {"type": "dependency_edge_present", "edges": [["api.py", "service.py"], ["service.py", "models.py"]]},
        ]),
        t,
        evidence(),
    )
    assert result["passed"] is True

    # 缺一条边（service.py 没被分析）→ 失败。
    t2 = trace(
        invoke=response(
            "finished",
            steps=[step("get_python_dependencies", tool_result=deps_api)],
            executor_state=exec_state(total=1, completed=1),
        )
    )
    result = score_task(
        _verify_task([
            {"type": "dependency_edge_present", "edges": [["api.py", "service.py"], ["service.py", "models.py"]]},
        ]),
        t2,
        evidence(),
    )
    assert result["passed"] is False


def test_verify_diff_presence():
    before_text = "返回 a 和 b 的乘积"
    after_text = "返回两个数的乘积"
    t = trivial_trace()
    task = _verify_task([
        {"type": "diff_presence", "path": "math_utils.py",
         "added_contains": ["返回两个数的乘积"],
         "removed_contains": ["返回 a 和 b 的乘积"]},
    ])
    assert score_task(
        task, t,
        evidence(before={"math_utils.py": file_entry(before_text)},
                 after={"math_utils.py": file_entry(after_text)}),
    )["passed"] is True
    assert not score_task(
        task, t,
        evidence(before={"math_utils.py": file_entry(after_text)},
                 after={"math_utils.py": file_entry(after_text)}),
    )["passed"]


def test_verify_unknown_type_reported():
    result = score_task(
        _verify_task([{"type": "no_such_type", "path": "f.py"}]),
        trivial_trace(),
        evidence(),
    )
    assert result["passed"] is False
    assert any("未知 verify.type" in check["detail"] for check in result["checks"])


def test_verify_item_file_content_contains_with_crlf_normalization():
    """evidence 内容做了 \r\n → \n 规范化。"""
    item = {"type": "file_content_contains", "path": "f.py", "contains": ["行二"], "not_contains": []}
    passed, _ = check_verify_item(
        item,
        {},
        trivial_trace(),
        {"after": {"f.py": {"exists": True, "sha256": "x", "content": "行一\r\n行二\r\n"}}},
    )
    assert passed is True


# ---------------------------------------------------------------- 纯函数性质


def test_scoring_is_deterministic():
    task = _verify_task([
        {"type": "file_content_contains", "path": "f.py", "contains": ["内容A"], "not_contains": []},
    ])
    t = trivial_trace()
    ev = evidence(after={"f.py": file_entry("内容A")})
    first = score_task(task, t, ev, duration_ms=1.0)
    second = score_task(task, t, ev, duration_ms=1.0)
    assert first == second


def test_score_task_never_raises_on_garbage_input():
    """评分器对畸形输入不抛异常（runner/offline 依赖这一点）。"""
    result = score_task({}, {}, {})
    assert result["passed"] is False
    assert result["metrics"]["final_status"] is None
    result = score_task(
        {"id": "x", "expected": {}, "approval": {}, "verify": [{"type": "file_exists"}]},
        {"invoke": "不是字典", "resumes": "不是列表"},
        {"before": "x", "approval": [], "after": None},
    )
    assert result["passed"] is False
