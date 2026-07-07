from fastapi.testclient import TestClient

import main
from app.memory import conversation_store


def use_temp_conversation_dir(tmp_path, monkeypatch):
    """
    将 conversation_store 的存储目录临时切换到 pytest 临时目录。
    避免测试污染真实 workspace/.conversations。
    """
    temp_dir = tmp_path / ".conversations"
    monkeypatch.setattr(conversation_store, "CONVERSATION_DIR", temp_dir)
    return temp_dir


def test_conversations_api_create_append_read_list(tmp_path, monkeypatch):
    """
    测试 conversations API：
    创建会话、追加消息、读取详情、查看列表。
    """
    use_temp_conversation_dir(tmp_path, monkeypatch)

    client = TestClient(main.app)

    create_resp = client.post(
        "/conversations",
        json={
            "title": "API Conversation Test",
            "metadata": {
                "source": "pytest",
            },
        },
    )

    assert create_resp.status_code == 200

    created = create_resp.json()
    conversation_id = created["conversation_id"]

    assert conversation_id.startswith("conv_")
    assert created["title"] == "API Conversation Test"

    append_resp = client.post(
        f"/conversations/{conversation_id}/messages",
        json={
            "role": "user",
            "content": "hello memory api",
            "metadata": {
                "source": "pytest",
            },
        },
    )

    assert append_resp.status_code == 200
    assert append_resp.json()["message"]["content"] == "hello memory api"

    detail_resp = client.get(f"/conversations/{conversation_id}")

    assert detail_resp.status_code == 200

    detail = detail_resp.json()

    assert detail["conversation_id"] == conversation_id
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["role"] == "user"

    list_resp = client.get("/conversations")

    assert list_resp.status_code == 200

    conversations = list_resp.json()["conversations"]
    conversation_ids = {
        item["conversation_id"]
        for item in conversations
    }

    assert conversation_id in conversation_ids


def test_chat_memory_creates_conversation_and_calls_llm(tmp_path, monkeypatch):
    """
    测试 /chat/memory：
    不传 conversation_id 时自动创建新会话，
    并且会把历史 messages 传给 LLM。
    """
    use_temp_conversation_dir(tmp_path, monkeypatch)

    captured = {}

    def fake_chat(messages, max_tokens=800):
        captured["messages"] = messages
        captured["max_tokens"] = max_tokens
        return "fake assistant answer"

    monkeypatch.setattr(main.llm, "chat", fake_chat)

    client = TestClient(main.app)

    resp = client.post(
        "/chat/memory",
        json={
            "message": "remember day07 memory",
            "title": "Memory Chat Test",
            "max_tokens": 123,
            "metadata": {
                "source": "pytest",
            },
        },
    )

    assert resp.status_code == 200

    data = resp.json()

    assert data["conversation_id"].startswith("conv_")
    assert data["title"] == "Memory Chat Test"
    assert data["answer"] == "fake assistant answer"
    assert data["message_count"] == 2

    assert captured["max_tokens"] == 123
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][-1]["role"] == "user"
    assert captured["messages"][-1]["content"] == "remember day07 memory"

    saved = conversation_store.read_conversation(data["conversation_id"])

    assert len(saved["messages"]) == 2
    assert saved["messages"][0]["role"] == "user"
    assert saved["messages"][1]["role"] == "assistant"
    assert saved["messages"][1]["content"] == "fake assistant answer"

    assert data["context_stats"]["total_messages"] >= 2
    assert data["context_stats"]["used_messages"] >= 2
    assert data["context_stats"]["estimated_input_tokens"] > 0


def test_chat_memory_continues_existing_conversation(tmp_path, monkeypatch):
    """
    测试 /chat/memory：
    传入已有 conversation_id 时，能够继续同一个会话。
    """
    use_temp_conversation_dir(tmp_path, monkeypatch)

    def fake_chat(messages, max_tokens=800):
        return f"fake answer for: {messages[-1]['content']}"

    monkeypatch.setattr(main.llm, "chat", fake_chat)

    client = TestClient(main.app)

    first_resp = client.post(
        "/chat/memory",
        json={
            "message": "first user message",
            "title": "Continue Memory Test",
        },
    )

    assert first_resp.status_code == 200

    conversation_id = first_resp.json()["conversation_id"]

    second_resp = client.post(
        "/chat/memory",
        json={
            "conversation_id": conversation_id,
            "message": "second user message",
        },
    )

    assert second_resp.status_code == 200

    second_data = second_resp.json()

    assert second_data["conversation_id"] == conversation_id
    assert second_data["message_count"] == 4
    assert second_data["answer"] == "fake answer for: second user message"

    saved = conversation_store.read_conversation(conversation_id)

    assert len(saved["messages"]) == 4
    assert saved["messages"][0]["content"] == "first user message"
    assert saved["messages"][2]["content"] == "second user message"

    assert second_data["context_stats"]["total_messages"] >= 4
    assert second_data["context_stats"]["used_messages"] >= 2
    assert second_data["context_stats"]["estimated_input_tokens"] > 0