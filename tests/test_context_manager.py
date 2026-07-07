from app.memory.context_manager import (
    build_context_window,
    estimate_text_tokens,
    normalize_messages,
    trim_messages_by_count,
    trim_messages_by_token_budget,
)


def test_estimate_text_tokens_handles_english_and_chinese():
    """
    测试 token 粗略估算能同时处理英文和中文。
    """
    assert estimate_text_tokens("hello world") >= 1
    assert estimate_text_tokens("你好世界") >= 4


def test_normalize_messages_filters_invalid_messages():
    """
    测试非法 role 或空内容会被过滤。
    """
    raw_messages = [
        {
            "role": "user",
            "content": "hello",
            "created_at": "now",
            "metadata": {},
        },
        {
            "role": "invalid",
            "content": "bad role",
        },
        {
            "role": "assistant",
            "content": "",
        },
    ]

    messages = normalize_messages(raw_messages)

    assert messages == [
        {
            "role": "user",
            "content": "hello",
        }
    ]


def test_trim_messages_by_count_keeps_system_and_recent_history():
    """
    测试按数量裁剪时：
    - system 保留
    - 只保留最近的历史消息
    """
    messages = [
        {
            "role": "system",
            "content": "system prompt",
        },
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
    ]

    trimmed = trim_messages_by_count(
        messages=messages,
        max_history_messages=2,
    )

    assert len(trimmed) == 3
    assert trimmed[0]["role"] == "system"
    assert trimmed[1]["content"] == "message 2"
    assert trimmed[2]["content"] == "message 3"


def test_trim_messages_by_token_budget_keeps_recent_messages():
    """
    测试按 token 预算裁剪时，优先保留最近消息。
    """
    messages = [
        {
            "role": "system",
            "content": "system",
        },
        {
            "role": "user",
            "content": "old message " * 100,
        },
        {
            "role": "assistant",
            "content": "middle message " * 100,
        },
        {
            "role": "user",
            "content": "latest message",
        },
    ]

    trimmed = trim_messages_by_token_budget(
        messages=messages,
        max_context_tokens=80,
        reserved_output_tokens=20,
        safety_margin_tokens=10,
    )

    contents = [
        message["content"]
        for message in trimmed
    ]

    assert "system" in contents
    assert "latest message" in contents


def test_build_context_window_returns_stats():
    """
    测试 build_context_window 能返回上下文 messages 和统计信息。
    """
    raw_messages = [
        {
            "role": "user",
            "content": "first",
            "created_at": "now",
            "metadata": {},
        },
        {
            "role": "assistant",
            "content": "second",
            "created_at": "now",
            "metadata": {},
        },
        {
            "role": "user",
            "content": "third",
            "created_at": "now",
            "metadata": {},
        },
    ]

    context = build_context_window(
        raw_messages=raw_messages,
        system_message="system prompt",
        max_history_messages=2,
        max_context_tokens=1000,
        reserved_output_tokens=100,
    )

    assert context["total_messages"] == 4
    assert context["used_messages"] == 3
    assert context["dropped_messages"] == 1
    assert context["estimated_input_tokens"] > 0

    assert context["messages"][0]["role"] == "system"
    assert context["messages"][1]["content"] == "second"
    assert context["messages"][2]["content"] == "third"