from app.agent.pending_execution_store import (
    read_pending_execution_context,
    save_pending_execution_context,
)


def test_pending_context_persists_pre_approval_steps(tmp_path):
    steps = [
        {"step": 1, "type": "tool_call", "tool_name": "read_file"},
        {"step": 2, "type": "approval_required", "tool_name": "edit_file"},
    ]

    save_pending_execution_context(
        approval_id="approval_test",
        task_plan={"steps": []},
        executor_state={"status": "waiting_approval"},
        max_steps=8,
        steps=steps,
        store_dir=tmp_path,
    )

    saved = read_pending_execution_context(
        "approval_test",
        store_dir=tmp_path,
    )

    assert saved is not None
    assert saved["version"] == "1.1"
    assert saved["steps"] == steps
