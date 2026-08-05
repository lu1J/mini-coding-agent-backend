from app.agent.pending_execution_store import (
    delete_pending_execution_context,
    read_pending_execution_context,
    save_pending_execution_context,
)


def test_pending_execution_context_round_trip(tmp_path):
    path = save_pending_execution_context(
        approval_id="approval_test_001",
        task_plan={"objective": "test"},
        executor_state={"status": "waiting_approval"},
        max_steps=8,
        store_dir=tmp_path,
    )

    assert path.endswith("approval_test_001.json")
    loaded = read_pending_execution_context(
        "approval_test_001",
        store_dir=tmp_path,
    )
    assert loaded["task_plan"]["objective"] == "test"
    assert loaded["executor_state"]["status"] == "waiting_approval"
    assert loaded["max_steps"] == 8


def test_delete_pending_execution_context(tmp_path):
    save_pending_execution_context(
        approval_id="approval_test_002",
        task_plan={},
        executor_state={},
        max_steps=5,
        store_dir=tmp_path,
    )
    delete_pending_execution_context(
        "approval_test_002",
        store_dir=tmp_path,
    )
    assert read_pending_execution_context(
        "approval_test_002",
        store_dir=tmp_path,
    ) is None


def test_invalid_approval_id_is_rejected(tmp_path):
    try:
        save_pending_execution_context(
            approval_id="///",
            task_plan={},
            executor_state={},
            max_steps=5,
            store_dir=tmp_path,
        )
    except ValueError as error:
        assert "approval_id" in str(error)
    else:
        raise AssertionError("应拒绝非法 approval_id")
