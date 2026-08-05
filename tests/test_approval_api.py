from fastapi.testclient import TestClient

import main


client = TestClient(main.app)



def test_execute_approval_returns_404_when_action_not_found(
    monkeypatch,
):
    """
    审批记录不存在时，接口应该返回 404，
    不能因为服务层返回 not_found 而产生 500。
    """

    def fake_execute_verified_approval(
        *,
        approval_id: str,
        approved: bool,
    ) -> dict:
        return {
            "status": "not_found",
            "message": "待确认动作不存在或已经处理。",
            "approval_id": approval_id,
        }

    monkeypatch.setattr(
        main,
        "execute_verified_approval",
        fake_execute_verified_approval,
    )

    response = client.post(
        "/agent/approvals/not-exist/execute",
        json={
            "approved": True,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "待确认动作不存在或已经处理。"
    )