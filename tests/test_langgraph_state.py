from app.agent.langgraph_state import build_graph_event
from app.agent.langgraph_service import new_thread_id


def test_new_thread_id_has_graph_prefix():
    thread_id = new_thread_id()
    assert thread_id.startswith("graph_")
    assert len(thread_id) > len("graph_")


def test_build_graph_event_keeps_payload():
    event = build_graph_event("plan_created", risk_level="high")
    assert event == {
        "event": "plan_created",
        "risk_level": "high",
    }
