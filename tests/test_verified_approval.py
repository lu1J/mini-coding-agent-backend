import app.agent.verified_approval as approval


def pending_action():
    return {
        "approval_id": "approval_20260805_010000_abcdef",
        "agent_name": "CodeAgent",
        "user_message": "请修改 demo/main.py",
        "tool_name": "edit_file",
        "tool_args": {
            "path": "demo/main.py",
            "old_text": "a",
            "new_text": "b",
        },
        "risk_level": "high",
    }


def test_rejected_action_is_deleted(monkeypatch):
    deleted = []
    monkeypatch.setattr(approval, "read_pending_action", lambda _: pending_action())
    monkeypatch.setattr(approval, "delete_pending_action", deleted.append)

    result = approval.execute_verified_approval(
        approval_id="approval_20260805_010000_abcdef",
        approved=False,
    )

    assert result["status"] == "rejected"
    assert deleted == ["approval_20260805_010000_abcdef"]


def test_policy_recheck_can_block_stale_action(monkeypatch):
    monkeypatch.setattr(approval, "read_pending_action", lambda _: pending_action())
    monkeypatch.setattr(approval, "delete_pending_action", lambda _: None)
    monkeypatch.setattr(approval, "build_task_plan", lambda _: {})
    monkeypatch.setattr(
        approval,
        "evaluate_tool_policy",
        lambda **kwargs: {
            "decision": approval.POLICY_BLOCK,
            "tool_name": "edit_file",
            "reasons": [],
            "violations": [{"code": "missing_plan_scope", "message": "无范围"}],
        },
    )

    result = approval.execute_verified_approval(
        approval_id="approval_20260805_010000_abcdef",
        approved=True,
    )

    assert result["status"] == "policy_blocked"
    assert result["success"] is False


def test_verification_failure_does_not_resume(monkeypatch):
    action = pending_action()
    monkeypatch.setattr(approval, "read_pending_action", lambda _: action)
    monkeypatch.setattr(approval, "delete_pending_action", lambda _: None)
    monkeypatch.setattr(
        approval,
        "build_task_plan",
        lambda _: {
            "intents": ["edit"],
            "target_paths": ["demo/main.py"],
            "suggested_tools": ["edit_file"],
            "steps": [{"suggested_tool": "edit_file"}],
        },
    )
    monkeypatch.setattr(
        approval,
        "evaluate_tool_policy",
        lambda **kwargs: {"decision": "require_approval"},
    )
    monkeypatch.setattr(
        approval,
        "_load_code_agent_runtime",
        lambda: (
            {"edit_file": lambda **kwargs: "ok"},
            lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("不应续跑")
            ),
        ),
    )
    monkeypatch.setattr(
        approval,
        "execute_verified_change",
        lambda **kwargs: {
            "success": False,
            "status": "failed_rolled_back",
            "rollback": {"success": True},
            "tool_execution": {"result": "修改完成但验证失败"},
        },
    )

    result = approval.execute_verified_approval(
        approval_id="approval_20260805_010000_abcdef",
        approved=True,
    )

    assert result["status"] == "verification_failed"
    assert result["resume_result"] is None


def test_verified_change_resumes_agent(monkeypatch):
    action = pending_action()
    monkeypatch.setattr(approval, "read_pending_action", lambda _: action)
    monkeypatch.setattr(approval, "delete_pending_action", lambda _: None)
    monkeypatch.setattr(
        approval,
        "build_task_plan",
        lambda _: {
            "intents": ["edit", "test"],
            "target_paths": ["demo/main.py"],
            "suggested_tools": ["edit_file", "run_command"],
            "steps": [{"suggested_tool": "edit_file"}],
        },
    )
    monkeypatch.setattr(
        approval,
        "evaluate_tool_policy",
        lambda **kwargs: {"decision": "require_approval"},
    )
    monkeypatch.setattr(
        approval,
        "_load_code_agent_runtime",
        lambda: (
            {"edit_file": lambda **kwargs: "ok"},
            lambda **kwargs: {
                "status": "finished",
                "answer": "完成",
                "steps": [],
            },
        ),
    )
    monkeypatch.setattr(
        approval,
        "execute_verified_change",
        lambda **kwargs: {
            "success": True,
            "status": "passed",
            "changed_paths": ["demo/main.py"],
            "diff": {"total_added_lines": 1, "total_removed_lines": 1},
            "tool_execution": {"result": "文件修改成功"},
        },
    )

    result = approval.execute_verified_approval(
        approval_id="approval_20260805_010000_abcdef",
        approved=True,
    )

    assert result["status"] == "approved"
    assert result["success"] is True
    assert result["resume_result"]["status"] == "finished"
