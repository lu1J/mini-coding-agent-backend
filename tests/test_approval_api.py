from fastapi.testclient import TestClient

import main


def test_execute_approval_returns_404_when_action_not_found(
    monkeypatch,
):
    """
    审批记录不存在时，接口应该返回 404，
    不能因为对 None 调用 get() 而返回 500。
    """

    monkeypatch.setattr(
        main,
        "read_pending_action",
        lambda approval_id: None,
    )

    client = TestClient(main.app)

    response = client.post(
        "/agent/approvals/approval_missing_test/execute",
        json={
            "approved": True,
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "待确认动作不存在或已处理",
    }