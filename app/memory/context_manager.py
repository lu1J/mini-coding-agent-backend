from typing import Any


VALID_MESSAGE_ROLES = {"system", "user", "assistant", "tool"}

DEFAULT_MAX_HISTORY_MESSAGES = 20
DEFAULT_MAX_CONTEXT_TOKENS = 6000
DEFAULT_RESERVED_OUTPUT_TOKENS = 800
DEFAULT_SAFETY_MARGIN_TOKENS = 200

MESSAGE_TOKEN_OVERHEAD = 4


def estimate_text_tokens(text: str) -> int:
    """
    粗略估算文本 token 数量。

    说明：
    - 英文大约 4 个字符约等于 1 个 token
    - 中文通常 1 个汉字接近 1 个 token
    - 这里只做工程上的粗略估算，不追求和真实 tokenizer 完全一致
    """
    if not text:
        return 0

    ascii_count = 0
    non_ascii_count = 0

    for char in text:
        if ord(char) < 128:
            ascii_count += 1
        else:
            non_ascii_count += 1

    estimated = ascii_count // 4 + non_ascii_count

    return max(1, estimated)


def estimate_message_tokens(message: dict[str, str]) -> int:
    """
    粗略估算一条 message 的 token 数量。
    """
    role = message.get("role", "")
    content = message.get("content", "")

    return (
        estimate_text_tokens(role)
        + estimate_text_tokens(content)
        + MESSAGE_TOKEN_OVERHEAD
    )


def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    """
    粗略估算多条 messages 的 token 数量。
    """
    return sum(
        estimate_message_tokens(message)
        for message in messages
    )


def normalize_message(raw_message: dict[str, Any]) -> dict[str, str] | None:
    """
    将本地保存的 message 转换成 LLM 可用格式。

    只保留：
    - role
    - content

    丢弃：
    - created_at
    - metadata
    """
    role = raw_message.get("role")
    content = raw_message.get("content")

    if role not in VALID_MESSAGE_ROLES:
        return None

    if not isinstance(content, str) or not content.strip():
        return None

    return {
        "role": role,
        "content": content,
    }


def normalize_messages(raw_messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """
    批量规范化 messages。
    """
    messages: list[dict[str, str]] = []

    for raw_message in raw_messages:
        message = normalize_message(raw_message)
        if message:
            messages.append(message)

    return messages


def trim_messages_by_count(
    messages: list[dict[str, str]],
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
) -> list[dict[str, str]]:
    """
    按消息数量裁剪历史消息。

    规则：
    - system 消息保留在前面
    - 非 system 消息只保留最近 max_history_messages 条
    """
    safe_max = max(1, max_history_messages)

    system_messages = [
        message
        for message in messages
        if message["role"] == "system"
    ]

    history_messages = [
        message
        for message in messages
        if message["role"] != "system"
    ]

    return system_messages + history_messages[-safe_max:]


def trim_messages_by_token_budget(
    messages: list[dict[str, str]],
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    reserved_output_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS,
    safety_margin_tokens: int = DEFAULT_SAFETY_MARGIN_TOKENS,
) -> list[dict[str, str]]:
    """
    按 token 预算裁剪 messages。

    规则：
    - system 消息优先保留
    - 历史消息从最近的开始往前选
    - 尽量不超过输入 token 预算
    """
    safe_max_context_tokens = max(1, max_context_tokens)
    safe_reserved_output_tokens = max(0, reserved_output_tokens)
    safe_safety_margin_tokens = max(0, safety_margin_tokens)

    input_budget = (
        safe_max_context_tokens
        - safe_reserved_output_tokens
        - safe_safety_margin_tokens
    )

    input_budget = max(1, input_budget)

    system_messages = [
        message
        for message in messages
        if message["role"] == "system"
    ]

    history_messages = [
        message
        for message in messages
        if message["role"] != "system"
    ]

    selected_reversed: list[dict[str, str]] = []

    used_tokens = estimate_messages_tokens(system_messages)

    for message in reversed(history_messages):
        message_tokens = estimate_message_tokens(message)

        if used_tokens + message_tokens <= input_budget:
            selected_reversed.append(message)
            used_tokens += message_tokens
            continue

        # 如果一条历史都还没选，至少保留最近一条，避免上下文为空
        if not selected_reversed:
            selected_reversed.append(message)

        break

    selected_history = list(reversed(selected_reversed))

    return system_messages + selected_history


def build_context_window(
    raw_messages: list[dict[str, Any]],
    system_message: str | None = None,
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    reserved_output_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """
    构建发送给 LLM 的上下文窗口。

    当前策略：
    1. 添加 system message
    2. 规范化历史消息
    3. 先按消息数量裁剪
    4. 再按 token 预算裁剪
    5. 返回 messages 和统计信息
    """
    messages: list[dict[str, str]] = []

    if system_message:
        messages.append({
            "role": "system",
            "content": system_message,
        })

    normalized_history = normalize_messages(raw_messages)
    messages.extend(normalized_history)

    after_count_trim = trim_messages_by_count(
        messages=messages,
        max_history_messages=max_history_messages,
    )

    final_messages = trim_messages_by_token_budget(
        messages=after_count_trim,
        max_context_tokens=max_context_tokens,
        reserved_output_tokens=reserved_output_tokens,
    )

    total_messages = len(messages)
    used_messages = len(final_messages)
    dropped_messages = max(0, total_messages - used_messages)

    return {
        "messages": final_messages,
        "total_messages": total_messages,
        "used_messages": used_messages,
        "dropped_messages": dropped_messages,
        "estimated_input_tokens": estimate_messages_tokens(final_messages),
        "max_context_tokens": max_context_tokens,
        "reserved_output_tokens": reserved_output_tokens,
    }