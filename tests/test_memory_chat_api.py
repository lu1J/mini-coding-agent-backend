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

    assert data["context_stats"]["summary_used"] is False


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

    assert second_data["context_stats"]["summary_used"] is False


def test_conversation_summary_api_and_memory_chat_uses_summary(tmp_path, monkeypatch):
    """
    测试 summary API：
    - 可以保存 summary
    - 可以读取 summary
    - /chat/memory 会把 summary 注入上下文
    """
    use_temp_conversation_dir(tmp_path, monkeypatch)

    captured = {}

    def fake_chat(messages, max_tokens=800):
        captured["messages"] = messages
        return "fake answer with summary"

    monkeypatch.setattr(main.llm, "chat", fake_chat)

    client = TestClient(main.app)

    create_resp = client.post(
        "/conversations",
        json={
            "title": "Summary API Test",
        },
    )

    assert create_resp.status_code == 200

    conversation_id = create_resp.json()["conversation_id"]

    update_summary_resp = client.put(
        f"/conversations/{conversation_id}/summary",
        json={
            "content": "用户正在开发 Mini Coding Agent，并且正在实现 Summary Memory。",
            "source_message_count": 3,
        },
    )

    assert update_summary_resp.status_code == 200

    updated_summary = update_summary_resp.json()["summary"]

    assert "Mini Coding Agent" in updated_summary["content"]
    assert updated_summary["source_message_count"] == 3
    assert updated_summary["updated_at"] is not None

    get_summary_resp = client.get(
        f"/conversations/{conversation_id}/summary",
    )

    assert get_summary_resp.status_code == 200

    summary = get_summary_resp.json()["summary"]

    assert "Summary Memory" in summary["content"]

    chat_resp = client.post(
        "/chat/memory",
        json={
            "conversation_id": conversation_id,
            "message": "我现在在做什么？",
        },
    )

    assert chat_resp.status_code == 200

    data = chat_resp.json()

    assert data["answer"] == "fake answer with summary"
    assert data["context_stats"]["summary_used"] is True

    sent_messages = captured["messages"]

    summary_messages = [
        message
        for message in sent_messages
        if message["role"] == "system"
        and "较早对话历史的摘要" in message["content"]
    ]

    assert len(summary_messages) == 1
    assert "Mini Coding Agent" in summary_messages[0]["content"]


def test_conversation_summary_refresh_api(tmp_path, monkeypatch):
    """
    测试 summary refresh API：
    - 自动读取未摘要消息
    - 调用 LLM 生成摘要
    - 保存 summary
    - 更新 source_message_count
    """
    use_temp_conversation_dir(tmp_path, monkeypatch)

    captured = {}

    def fake_chat(messages, max_tokens=600):
        captured["messages"] = messages
        captured["max_tokens"] = max_tokens
        return "用户正在开发 Mini Coding Agent，并且正在实现自动摘要记忆。"

    monkeypatch.setattr(main.llm, "chat", fake_chat)

    client = TestClient(main.app)

    create_resp = client.post(
        "/conversations",
        json={
            "title": "Summary Refresh Test",
        },
    )

    assert create_resp.status_code == 200

    conversation_id = create_resp.json()["conversation_id"]

    client.post(
        f"/conversations/{conversation_id}/messages",
        json={
            "role": "user",
            "content": "我正在开发 Mini Coding Agent。",
        },
    )

    client.post(
        f"/conversations/{conversation_id}/messages",
        json={
            "role": "assistant",
            "content": "好的，我会记住这个项目背景。",
        },
    )

    refresh_resp = client.post(
        f"/conversations/{conversation_id}/summary/refresh",
        json={
            "force": False,
            "max_new_messages": 30,
            "max_tokens": 500,
        },
    )

    assert refresh_resp.status_code == 200

    data = refresh_resp.json()

    assert data["refreshed"] is True
    assert data["processed_message_count"] == 2
    assert data["source_message_count"] == 2
    assert "Mini Coding Agent" in data["summary"]["content"]

    assert captured["max_tokens"] == 500
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][1]["role"] == "user"
    assert "新增对话消息" in captured["messages"][1]["content"]

    saved = conversation_store.read_conversation(conversation_id)

    assert saved["summary"]["source_message_count"] == 2
    assert "自动摘要记忆" in saved["summary"]["content"]


def test_conversation_summary_refresh_no_new_messages(tmp_path, monkeypatch):
    """
    测试没有新消息时，summary refresh 不会重复调用 LLM。
    """
    use_temp_conversation_dir(tmp_path, monkeypatch)

    def fake_chat(messages, max_tokens=600):
        raise AssertionError("没有新消息时不应该调用 LLM")

    monkeypatch.setattr(main.llm, "chat", fake_chat)

    client = TestClient(main.app)

    create_resp = client.post(
        "/conversations",
        json={
            "title": "No New Summary Test",
        },
    )

    conversation_id = create_resp.json()["conversation_id"]

    client.put(
        f"/conversations/{conversation_id}/summary",
        json={
            "content": "已有摘要。",
            "source_message_count": 0,
        },
    )

    refresh_resp = client.post(
        f"/conversations/{conversation_id}/summary/refresh",
        json={
            "force": False,
        },
    )

    assert refresh_resp.status_code == 200

    data = refresh_resp.json()

    assert data["refreshed"] is False
    assert data["processed_message_count"] == 0
    assert data["reason"] == "没有需要摘要的新消息。"