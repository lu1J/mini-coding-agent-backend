from app.agent.execution_policy_guard import (
    POLICY_ALLOW,
    POLICY_ALLOW_WITH_AUDIT,
    POLICY_BLOCK,
    POLICY_REQUIRE_APPROVAL,
    evaluate_tool_policy,
)
from app.agent.plan_execution_audit import build_plan_execution_audit
from app.agent.plan_parameter_audit import extract_planned_target_paths
from app.tools.file_tools import (
    WORKSPACE_ROOT,
    canonical_workspace_relative,
)


def make_plan(
    *,
    tools: list[str],
    targets: list[str],
    risk_level: str = "low",
) -> dict:
    return {
        "suggested_tools": tools,
        "target_paths": targets,
        "risk_level": risk_level,
        "needs_approval": risk_level == "high",
        "steps": [
            {"suggested_tool": tool_name}
            for tool_name in tools
        ],
    }


def test_planned_read_inside_scope_is_allowed():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["read_file"],
            targets=["demo_project/models.py"],
        ),
        tool_name="read_file",
        tool_args={"path": "demo_project/models.py"},
        risk_level="low",
    )

    assert decision["decision"] == POLICY_ALLOW
    assert decision["matched_paths"] == ["demo_project/models.py"]


def test_read_outside_scope_is_allowed_with_audit():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["read_file"],
            targets=["demo_project/models.py"],
        ),
        tool_name="read_file",
        tool_args={"path": "demo_project/service.py"},
        risk_level="low",
    )

    assert decision["decision"] == POLICY_ALLOW_WITH_AUDIT
    assert decision["outside_paths"] == ["demo_project/service.py"]


def test_unplanned_low_risk_tool_is_allowed_with_audit():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["read_file"],
            targets=["demo_project/models.py"],
        ),
        tool_name="search_code",
        tool_args={"path": "demo_project", "query": "User"},
        risk_level="low",
    )

    assert decision["decision"] == POLICY_ALLOW_WITH_AUDIT
    assert decision["planned"] is False


def test_planned_write_inside_scope_requires_approval():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["edit_file"],
            targets=["demo_project/models.py"],
            risk_level="high",
        ),
        tool_name="edit_file",
        tool_args={
            "path": r"demo_project\models.py",
            "old_text": "name",
            "new_text": "username",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_REQUIRE_APPROVAL
    assert decision["requires_approval"] is True
    assert decision["outside_paths"] == []


def test_unplanned_write_is_blocked_even_inside_scope():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["read_file"],
            targets=["demo_project/models.py"],
        ),
        tool_name="edit_file",
        tool_args={
            "path": "demo_project/models.py",
            "old_text": "name",
            "new_text": "username",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert any(
        item["code"] == "unplanned_risky_tool"
        for item in decision["violations"]
    )


def test_write_outside_scope_is_blocked():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["edit_file"],
            targets=["demo_project/models.py"],
            risk_level="high",
        ),
        tool_name="edit_file",
        tool_args={
            "path": "demo_project/service.py",
            "old_text": "name",
            "new_text": "username",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert decision["outside_paths"] == ["demo_project/service.py"]


def test_write_without_planned_target_is_blocked_fail_closed():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["write_new_file"],
            targets=[],
            risk_level="high",
        ),
        tool_name="write_new_file",
        tool_args={
            "path": "demo_project/config.py",
            "content": "DEBUG = False\n",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert any(
        item["code"] == "missing_plan_scope"
        for item in decision["violations"]
    )


def test_write_without_path_is_blocked_fail_closed():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["edit_file"],
            targets=["demo_project/models.py"],
            risk_level="high",
        ),
        tool_name="edit_file",
        tool_args={
            "old_text": "name",
            "new_text": "username",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert any(
        item["code"] == "missing_write_path"
        for item in decision["violations"]
    )


def test_planned_command_is_allowed():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["run_command"],
            targets=["demo_project/models.py"],
            risk_level="medium",
        ),
        tool_name="run_command",
        tool_args={
            "command": "python -m py_compile demo_project/models.py"
        },
        risk_level="medium",
    )

    assert decision["decision"] == POLICY_ALLOW


def test_planned_project_level_test_is_allowed_with_audit():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["run_command"],
            targets=["demo_project/models.py"],
            risk_level="medium",
        ),
        tool_name="run_command",
        tool_args={"command": "python -m pytest -q"},
        risk_level="medium",
    )

    assert decision["decision"] == POLICY_ALLOW_WITH_AUDIT


def test_unplanned_command_is_blocked():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["read_file"],
            targets=["demo_project/models.py"],
        ),
        tool_name="run_command",
        tool_args={"command": "python -m pytest -q"},
        risk_level="medium",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert any(
        item["code"] == "unplanned_command"
        for item in decision["violations"]
    )


def test_unclassified_high_risk_tool_is_blocked():
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["dangerous_custom_tool"],
            targets=["demo_project/models.py"],
            risk_level="high",
        ),
        tool_name="dangerous_custom_tool",
        tool_args={"path": "demo_project/models.py"},
        risk_level="high",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert any(
        item["code"] == "unclassified_high_risk_tool"
        for item in decision["violations"]
    )


def test_audit_summarizes_policy_guard_decisions():
    plan = make_plan(
        tools=["edit_file"],
        targets=["demo_project/models.py"],
        risk_level="high",
    )
    policy_decision = evaluate_tool_policy(
        task_plan=plan,
        tool_name="edit_file",
        tool_args={
            "path": "demo_project/service.py",
            "old_text": "name",
            "new_text": "username",
        },
        risk_level="high",
    )

    audit = build_plan_execution_audit(
        task_plan=plan,
        execution_steps=[
            {
                "step": 1,
                "type": "policy_blocked",
                "tool_name": "edit_file",
                "tool_args": {
                    "path": "demo_project/service.py"
                },
                "risk_level": "high",
                "success": False,
                "policy_decision": policy_decision,
            }
        ],
    )

    assert audit["status"] == "requires_review"
    assert audit["policy_guard"]["checked_count"] == 1
    assert audit["policy_guard"]["blocked_count"] == 1
    assert audit["outcome"]["tool_call_count"] == 0


# ---------------------------------------------------------------------------
# Day20 P0：canonical workspace-relative 路径归一（等价的同义表述必须匹配）
# ---------------------------------------------------------------------------


def test_canonical_workspace_prefix_and_dot_prefix_are_equivalent():
    """workspace/foo.py == ./foo.py == foo.py == .\\foo.py 归一为同一形式。"""
    plain = canonical_workspace_relative("demo_project/main.py")
    assert plain == "demo_project/main.py"

    for form in (
        "workspace/demo_project/main.py",
        "./demo_project/main.py",
        ".\\demo_project\\main.py",
        "demo_project//main.py",
    ):
        assert canonical_workspace_relative(form) == plain, form


def test_canonical_eval_case_forms_are_equivalent():
    """eval case 目录下，带 workspace 前缀的完整路径与工作区相对路径等价。"""
    case_planned = (
        "workspace/eval_cases/run_1/task_1/demo_project/math_utils.py"
    )
    case_requested = "eval_cases/run_1/task_1/demo_project/math_utils.py"
    canonical = canonical_workspace_relative(case_planned)

    assert canonical == "eval_cases/run_1/task_1/demo_project/math_utils.py"
    assert canonical_workspace_relative(case_requested) == canonical


def test_canonical_different_real_files_are_not_equivalent():
    """不同真实文件即使前缀相同也不能相互匹配。"""
    a = canonical_workspace_relative("demo_project/main.py")
    b = canonical_workspace_relative("demo_project/math_utils.py")

    assert a != b
    assert a is not None and b is not None
    assert not a.startswith(b + "/") and not b.startswith(a + "/")


def test_canonical_absolute_inside_workspace_is_folded_to_relative():
    """位于 workspace 内的绝对路径（真实 resolve 后）也归一为相对形式。"""
    absolute = str((WORKSPACE_ROOT / "demo_project" / "main.py").resolve())
    assert canonical_workspace_relative(absolute) == "demo_project/main.py"


def test_canonical_rejects_escape_paths():
    """../、../../ 与深层逃逸路径必须返回 None（fail closed）。"""
    for escape in (
        "../secret.py",
        "..\\..\\.env",
        "../../.env",
        "a/../../secret.py",
        "../workspace/secret.py",
        "/etc/passwd",
        "/",
        "",
        None,
    ):
        assert canonical_workspace_relative(escape) is None, repr(escape)


def test_extract_planned_target_paths_drops_escape_entries():
    """计划目标提取同样 canonical 化：越界条目被丢弃，等价形式被折叠。"""
    extracted = extract_planned_target_paths(
        {
            "target_paths": [
                "workspace/demo_project/main.py",
                "demo_project/main.py",
                "../../evil.py",
            ]
        }
    )

    assert extracted == ["demo_project/main.py"]


def test_policy_workspace_prefixed_plan_matches_relative_write():
    """P0 回归：Planner 计划带 workspace/ 前缀、工具调用不带前缀时，
    合法写路径必须到达 require_approval，而不是被误判 write_outside_scope。"""
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["edit_file"],
            targets=[
                "workspace/eval_cases/run_1/task_1/demo_project/math_utils.py"
            ],
            risk_level="high",
        ),
        tool_name="edit_file",
        tool_args={
            "path": "eval_cases/run_1/task_1/demo_project/math_utils.py",
            "old_text": "old",
            "new_text": "new",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_REQUIRE_APPROVAL, decision
    assert decision["requires_approval"] is True
    assert decision["outside_paths"] == []


def test_policy_equivalent_plan_and_write_forms_require_approval():
    """多种同义表述（workspace/、./、相对）都合法且互可匹配。"""
    plan_forms = [
        "workspace/demo_project/main.py",
        "./demo_project/main.py",
        "demo_project/main.py",
    ]
    request_forms = [
        "workspace/demo_project/main.py",
        "./demo_project/main.py",
        "demo_project/main.py",
    ]

    for planned in plan_forms:
        for requested in request_forms:
            decision = evaluate_tool_policy(
                task_plan=make_plan(
                    tools=["edit_file"],
                    targets=[planned],
                    risk_level="high",
                ),
                tool_name="edit_file",
                tool_args={
                    "path": requested,
                    "old_text": "old",
                    "new_text": "new",
                },
                risk_level="high",
            )
            assert decision["decision"] == POLICY_REQUIRE_APPROVAL, (
                planned,
                requested,
                decision,
            )
            assert decision["outside_paths"] == [], (planned, requested)


def test_policy_real_out_of_scope_write_still_blocked():
    """真正越界（计划内是 main.py，写入 service.py）仍然 POLICY_BLOCK。"""
    decision = evaluate_tool_policy(
        task_plan=make_plan(
            tools=["edit_file"],
            targets=["workspace/demo_project/main.py"],
            risk_level="high",
        ),
        tool_name="edit_file",
        tool_args={
            "path": "demo_project/service.py",
            "old_text": "old",
            "new_text": "new",
        },
        risk_level="high",
    )

    assert decision["decision"] == POLICY_BLOCK
    assert decision["violations"][0]["code"] == "write_outside_scope"


def test_policy_escape_write_still_blocked():
    """../ 逃逸写路径（新 canonical 层下）必须仍然被 block。"""
    for escape in ("../secret.py", "../../.env", "..\\..\\.env"):
        decision = evaluate_tool_policy(
            task_plan=make_plan(
                tools=["edit_file"],
                targets=["demo_project/main.py"],
                risk_level="high",
            ),
            tool_name="edit_file",
            tool_args={
                "path": escape,
                "old_text": "old",
                "new_text": "new",
            },
            risk_level="high",
        )

        assert decision["decision"] == POLICY_BLOCK, (escape, decision)
        assert any(
            item["code"] == "write_outside_scope"
            for item in decision["violations"]
        ), (escape, decision)


def test_policy_absolute_outside_write_still_blocked():
    """外部绝对路径 / Windows 盘符逃逸仍然被 block（不降级安全）。"""
    for escape in (
        "C:/Windows/System32/drivers/etc/hosts",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "E:/outside/secret.txt",
        "/etc/passwd",
    ):
        decision = evaluate_tool_policy(
            task_plan=make_plan(
                tools=["edit_file"],
                targets=["demo_project/main.py"],
                risk_level="high",
            ),
            tool_name="edit_file",
            tool_args={
                "path": escape,
                "old_text": "old",
                "new_text": "new",
            },
            risk_level="high",
        )

        assert decision["decision"] == POLICY_BLOCK, (escape, decision)
