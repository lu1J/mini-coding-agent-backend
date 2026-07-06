import pytest

from app.memory import conversation_store


def use_temp_conversation_dir(tmp_path, monkeypatch):
    """
    将 conversation_store 的存储目录临时切换到 pytest 的临时目录。

    这样测试不会污染真实的 workspace/.conversations。
    """
    temp_dir = tmp_path / ".conversations"
    monkeypatch.setattr(conversation_store, "CONVERSATION_DIR", temp_dir)
    return temp_dir


def test_create_conversation(tmp_path, monkeypatch):
    """
    测试能否创建一个新会话。
    """
    temp_dir = use_temp_conversation_dir(tmp_path, monkeypatch)

    conversation = conversation_store.create_conversation(
        title="Test Conversation",
        metadata={"source": "pytest"},
    )

    assert conversation["conversation_id"].startswith("conv_")
    assert conversation["title"] == "Test Conversation"
    assert conversation["metadata"]["source"] == "pytest"
    assert conversation["messages"] == []

    conversation_path = temp_dir / f"{conversation['conversation_id']}.json"
    assert conversation_path.exists()


def test_append_and_read_conversation_messages(tmp_path, monkeypatch):
    """
    测试能否向会话追加消息，并重新读取。
    """
    use_temp_conversation_dir(tmp_path, monkeypatch)

    conversation = conversation_store.create_conversation(title="Chat Test")
    conversation_id = conversation["conversation_id"]

    user_message = conversation_store.append_message(
        conversation_id=conversation_id,
        role="user",
        content="你好，我正在测试会话记忆。",
    )

    assistant_message = conversation_store.append_message(
        conversation_id=conversation_id,
        role="assistant",
        content="好的，我已经记录了这条消息。",
    )

    assert user_message["role"] == "user"
    assert assistant_message["role"] == "assistant"

    saved = conversation_store.read_conversation(conversation_id)

    assert saved["conversation_id"] == conversation_id
    assert len(saved["messages"]) == 2
    assert saved["messages"][0]["content"] == "你好，我正在测试会话记忆。"
    assert saved["messages"][1]["content"] == "好的，我已经记录了这条消息。"


def test_list_conversations(tmp_path, monkeypatch):
    """
    测试能否列出最近会话。
    """
    use_temp_conversation_dir(tmp_path, monkeypatch)

    conv1 = conversation_store.create_conversation(title="Conversation 1")
    conv2 = conversation_store.create_conversation(title="Conversation 2")

    conversation_store.append_message(
        conversation_id=conv1["conversation_id"],
        role="user",
        content="第一条会话消息",
    )

    conversation_store.append_message(
        conversation_id=conv2["conversation_id"],
        role="user",
        content="第二条会话消息",
    )

    conversations = conversation_store.list_conversations(limit=10)

    conversation_ids = {
        item["conversation_id"]
        for item in conversations
    }

    assert conv1["conversation_id"] in conversation_ids
    assert conv2["conversation_id"] in conversation_ids

    for item in conversations:
        assert "title" in item
        assert "message_count" in item
        assert "updated_at" in item


def test_build_messages_for_llm_with_sliding_window(tmp_path, monkeypatch):
    """
    测试能否把本地会话记录转换成 LLM messages 格式。

    当前版本使用简单滑动窗口，只保留最近 max_messages 条消息。
    """
    use_temp_conversation_dir(tmp_path, monkeypatch)

    conversation = conversation_store.create_conversation(title="LLM Messages")
    conversation_id = conversation["conversation_id"]

    conversation_store.append_message(
        conversation_id=conversation_id,
        role="user",
        content="第一轮用户消息",
    )
    conversation_store.append_message(
        conversation_id=conversation_id,
        role="assistant",
        content="第一轮助手回复",
    )
    conversation_store.append_message(
        conversation_id=conversation_id,
        role="user",
        content="第二轮用户消息",
    )

    messages = conversation_store.build_messages_for_llm(
        conversation_id=conversation_id,
        system_message="你是一个测试助手。",
        max_messages=2,
    )

    assert len(messages) == 3

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "你是一个测试助手。"

    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "第一轮助手回复"

    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "第二轮用户消息"


def test_validate_conversation_id_rejects_invalid_values():
    """
    测试非法 conversation_id 会被拒绝，防止路径穿越。
    """
    invalid_ids = [
        "",
        "abc_123",
        "conv_../secret",
        "conv_x/y",
        "conv_x\\y",
    ]

    for conversation_id in invalid_ids:
        with pytest.raises(ValueError):
            conversation_store.validate_conversation_id(conversation_id)


def test_read_missing_conversation_raises_file_not_found(tmp_path, monkeypatch):
    """
    测试读取不存在的会话时，会抛出 FileNotFoundError。
    """
    use_temp_conversation_dir(tmp_path, monkeypatch)

    with pytest.raises(FileNotFoundError):
        conversation_store.read_conversation("conv_20990101_000000_missing1")


def test_append_message_rejects_invalid_role(tmp_path, monkeypatch):
    """
    测试非法消息角色会被拒绝。
    """
    use_temp_conversation_dir(tmp_path, monkeypatch)

    conversation = conversation_store.create_conversation()
    conversation_id = conversation["conversation_id"]

    with pytest.raises(ValueError):
        conversation_store.append_message(
            conversation_id=conversation_id,
            role="invalid_role",
            content="这条消息不应该被保存。",
        )