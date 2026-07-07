from app.memory.summary_manager import (
    build_summary_prompt_messages,
    format_messages_for_summary,
    get_existing_summary_content,
    get_summary_source_message_count,
    get_unsummarized_messages,
    should_refresh_summary,
)


def test_get_existing_summary_content_handles_missing_summary():
    """
    测试缺少 summary 时返回空字符串。
    """
    conversation = {
        "messages": [],
    }

    assert get_existing_summary_content(conversation) == ""


def test_get_summary_source_message_count():
    """
    测试能读取 summary 覆盖的消息数量。
    """
    conversation = {
        "summary": {
            "source_message_count": 5,
        },
        "messages": [],
    }

    assert get_summary_source_message_count(conversation) == 5


def test_get_unsummarized_messages_uses_source_message_count():
    """
    测试只返回还没有被 summary 覆盖的新消息。
    """
    conversation = {
        "summary": {
            "source_message_count": 2,
        },
        "messages": [
            {
                "role": "user",
                "content": "message 1",
            },
            {
                "role": "assistant",
                "content": "message 2",
            },
            {
                "role": "user",
                "content": "message 3",
            },
            {
                "role": "assistant",
                "content": "message 4",
            },
        ],
    }

    new_messages, start_index, target_count = get_unsummarized_messages(
        conversation=conversation,
        max_new_messages=10,
    )

    assert start_index == 2
    assert target_count == 4
    assert len(new_messages) == 2
    assert new_messages[0]["content"] == "message 3"


def test_get_unsummarized_messages_force_rebuilds_from_start():
    """
    测试 force=True 时会从头开始摘要。
    """
    conversation = {
        "summary": {
            "source_message_count": 2,
        },
        "messages": [
            {
                "role": "user",
                "content": "message 1",
            },
            {
                "role": "assistant",
                "content": "message 2",
            },
        ],
    }

    new_messages, start_index, target_count = get_unsummarized_messages(
        conversation=conversation,
        max_new_messages=10,
        force=True,
    )

    assert start_index == 0
    assert target_count == 2
    assert len(new_messages) == 2


def test_format_messages_for_summary():
    """
    测试消息格式化。
    """
    messages = [
        {
            "role": "user",
            "content": "hello",
        },
        {
            "role": "assistant",
            "content": "hi",
        },
    ]

    formatted = format_messages_for_summary(
        messages=messages,
        start_index=2,
    )

    assert "3. user: hello" in formatted
    assert "4. assistant: hi" in formatted


def test_build_summary_prompt_messages():
    """
    测试摘要 prompt 构造。
    """
    messages = [
        {
            "role": "user",
            "content": "我正在开发 Mini Coding Agent。",
        }
    ]

    prompt_messages = build_summary_prompt_messages(
        existing_summary="用户在学习 Agent 开发。",
        new_messages=messages,
        start_index=0,
    )

    assert len(prompt_messages) == 2
    assert prompt_messages[0]["role"] == "system"
    assert prompt_messages[1]["role"] == "user"
    assert "已有摘要" in prompt_messages[1]["content"]
    assert "新增对话消息" in prompt_messages[1]["content"]
    assert "Mini Coding Agent" in prompt_messages[1]["content"]


def test_should_refresh_summary():
    """
    测试新增消息达到阈值时才刷新 summary。
    """
    conversation = {
        "summary": {
            "source_message_count": 1,
        },
        "messages": [
            {
                "role": "user",
                "content": "1",
            },
            {
                "role": "assistant",
                "content": "2",
            },
            {
                "role": "user",
                "content": "3",
            },
            {
                "role": "assistant",
                "content": "4",
            },
            {
                "role": "user",
                "content": "5",
            },
        ],
    }

    assert should_refresh_summary(
        conversation=conversation,
        min_new_messages=4,
    ) is True

    assert should_refresh_summary(
        conversation=conversation,
        min_new_messages=5,
    ) is False