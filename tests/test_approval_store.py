import pytest

from app.agent import approval_store


def test_save_and_read_pending_action():
    """
    pending action 应该可以保存并读取。
    """
    pending = approval_store.save_pending_action(
        agent_name="TestAgent",
        user_message="请创建一个文件",
        tool_name="write_new_file",
        tool_args={
            "path": "demo_project/test.py",
            "content": "print('hello')\n",
        },
        risk_level="high",
        reason="测试审批动作",
    )

    approval_id = pending["approval_id"]

    try:
        loaded = approval_store.read_pending_action(approval_id)

        assert loaded is not None
        assert loaded["approval_id"] == approval_id
        assert loaded["agent_name"] == "TestAgent"
        assert loaded["tool_name"] == "write_new_file"
        assert loaded["risk_level"] == "high"
        assert loaded["status"] == "pending"
        assert loaded["tool_args"]["path"] == "demo_project/test.py"

    finally:
        approval_store.delete_pending_action(approval_id)


def test_delete_pending_action():
    """
    delete_pending_action 删除后，read_pending_action 应该返回 None。
    """
    pending = approval_store.save_pending_action(
        agent_name="TestAgent",
        user_message="测试删除 pending action",
        tool_name="write_new_file",
        tool_args={
            "path": "demo_project/delete_me.py",
            "content": "print('delete')\n",
        },
        risk_level="high",
        reason="测试删除",
    )

    approval_id = pending["approval_id"]

    approval_store.delete_pending_action(approval_id)

    loaded = approval_store.read_pending_action(approval_id)

    assert loaded is None


def test_invalid_approval_id_rejected():
    """
    非法 approval_id 应该被拒绝，防止路径穿越。
    """
    with pytest.raises(ValueError):
        approval_store.read_pending_action("../bad_id")


def test_pending_action_contains_pending_path():
    """
    pending action 应该包含 pending_path，方便调试和前端展示。
    """
    pending = approval_store.save_pending_action(
        agent_name="TestAgent",
        user_message="测试 pending_path",
        tool_name="ensure_gitignore",
        tool_args={
            "cwd": "demo_project",
        },
        risk_level="high",
        reason="测试 pending_path",
    )

    approval_id = pending["approval_id"]

    try:
        assert "pending_path" in pending
        assert pending["pending_path"].startswith(".agent_pending")

    finally:
        approval_store.delete_pending_action(approval_id)