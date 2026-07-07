from typing import Any


SUMMARY_SYSTEM_PROMPT = """
你是一个会话摘要助手。

你的任务是根据已有摘要和新增对话消息，生成一份简洁、准确、可持续更新的长期记忆摘要。

要求：
1. 保留用户明确提到的长期偏好、项目背景、技术栈、当前进度和重要决策。
2. 删除寒暄、重复内容、无关细节和临时调试噪声。
3. 不要编造对话中没有出现的信息。
4. 输出中文摘要。
5. 摘要应适合在后续对话中作为长期上下文使用。
""".strip()


def safe_int(value: Any, default: int = 0) -> int:
    """
    安全转换整数。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_existing_summary_content(conversation: dict[str, Any]) -> str:
    """
    获取已有 summary content。
    """
    summary = conversation.get("summary", {})

    if not isinstance(summary, dict):
        return ""

    content = summary.get("content", "")

    if not isinstance(content, str):
        return ""

    return content.strip()


def get_summary_source_message_count(conversation: dict[str, Any]) -> int:
    """
    获取当前 summary 已经覆盖的消息数量。
    """
    summary = conversation.get("summary", {})

    if not isinstance(summary, dict):
        return 0

    return max(0, safe_int(summary.get("source_message_count"), default=0))


def get_unsummarized_messages(
    conversation: dict[str, Any],
    max_new_messages: int = 30,
    force: bool = False,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    获取还没有被 summary 覆盖的新消息。

    返回：
    - new_messages：本次需要摘要的新消息
    - start_index：从第几条消息开始
    - target_source_message_count：本次摘要完成后应覆盖的消息数量
    """
    messages = conversation.get("messages", [])

    if not isinstance(messages, list):
        return [], 0, 0

    safe_max_new_messages = max(1, max_new_messages)

    if force:
        start_index = 0
    else:
        start_index = get_summary_source_message_count(conversation)

    start_index = max(0, min(start_index, len(messages)))

    new_messages = messages[start_index:start_index + safe_max_new_messages]
    target_source_message_count = start_index + len(new_messages)

    return new_messages, start_index, target_source_message_count


def format_message_for_summary(message: dict[str, Any], index: int) -> str:
    """
    将单条消息格式化为摘要输入文本。
    """
    role = message.get("role", "unknown")
    content = message.get("content", "")

    if not isinstance(content, str):
        content = str(content)

    content = content.strip()

    return f"{index}. {role}: {content}"


def format_messages_for_summary(
    messages: list[dict[str, Any]],
    start_index: int = 0,
) -> str:
    """
    将多条消息格式化为摘要输入文本。
    """
    lines: list[str] = []

    for offset, message in enumerate(messages, start=start_index + 1):
        formatted = format_message_for_summary(message, index=offset)
        lines.append(formatted)

    return "\n".join(lines)


def build_summary_prompt_messages(
    existing_summary: str,
    new_messages: list[dict[str, Any]],
    start_index: int = 0,
) -> list[dict[str, str]]:
    """
    构造发送给 LLM 的 summary prompt messages。
    """
    formatted_messages = format_messages_for_summary(
        messages=new_messages,
        start_index=start_index,
    )

    if not formatted_messages:
        formatted_messages = "无新增消息。"

    user_prompt = f"""
已有摘要：
{existing_summary or "暂无"}

新增对话消息：
{formatted_messages}

请基于“已有摘要”和“新增对话消息”，生成更新后的长期记忆摘要。
如果已有摘要中仍然重要的信息没有被新增消息推翻，请保留。
如果新增消息提供了新的项目进度、技术决策或用户偏好，请合并进去。
""".strip()

    return [
        {
            "role": "system",
            "content": SUMMARY_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]


def should_refresh_summary(
    conversation: dict[str, Any],
    min_new_messages: int = 4,
) -> bool:
    """
    判断是否有足够的新消息值得刷新 summary。
    """
    messages = conversation.get("messages", [])

    if not isinstance(messages, list):
        return False

    source_message_count = get_summary_source_message_count(conversation)
    new_message_count = len(messages) - source_message_count

    return new_message_count >= min_new_messages