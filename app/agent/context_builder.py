"""Day17 上下文构造器（Context Builder）。

职责：
- 把 model_node 中分散的“模型请求消息拼装逻辑”抽成独立纯函数层；
- 对超长 role=tool 结果提供“请求侧压缩”（头部 + 省略标记 + 尾部）；
- 输出上下文规模估算与 debug metadata，供 graph_events 观测。

State 与 Context 分离：
- State（state["messages"] / checkpoint）保存系统真实拥有的完整信息：
  工具原始结果全文、审批记录、reflection、deferred feedback 一律原样落盘，
  本模块永不写入、永不修改它们；
- Context（本模块的输出）决定“这一轮模型应该看到什么”：只改变发送给
  LLM 的消息副本，绝不修改任何输入对象。

协议安全（对应 Day15 不变式）：
1. Context Completeness：system_prompt 与 user_message 永远位于头部；
2. 顺序严格保持：
   system_prompt → user_message → message_log → deferred_feedback
   → ephemeral executor_instruction
   executor_instruction 只能由调用方作为临时上下文拼入请求，绝不能持久化；
3. 只压缩 role=tool 消息的 content；role / tool_call_id 等协议字段不变；
   不删除任何消息；
4. 未闭环 batch 防御：若 message_log 尾部存在 assistant(tool_calls) 且其
   tool_call_id 没有全部得到 role=tool 响应，本模块不做任何压缩，并在
   context_meta 记录 protocol_guard_triggered=true。

token 估算复用 app/memory/context_manager（无依赖纯函数模块，直接复用不会
产生循环依赖；它是整个仓库已有的中英文混合估算实现，不重复造第二套）。
"""

from __future__ import annotations

from typing import Any

from app.memory.context_manager import (
    estimate_messages_tokens as estimate_messages_tokens,
)

DEFAULT_MAX_TOOL_CONTENT_CHARS = 4000
DEFAULT_TOOL_CONTENT_HEAD_CHARS = 1200
DEFAULT_TOOL_CONTENT_TAIL_CHARS = 600

_OMITTED_MARKER_TEMPLATE = "\n……[内容过长已省略：原文 {original_chars} 字符]\n……"


def compress_tool_content(
    content: str,
    *,
    max_chars: int = DEFAULT_MAX_TOOL_CONTENT_CHARS,
    head_chars: int = DEFAULT_TOOL_CONTENT_HEAD_CHARS,
    tail_chars: int = DEFAULT_TOOL_CONTENT_TAIL_CHARS,
) -> str:
    """压缩超长 role=tool 消息的 content 文本。

    规则：
    - 长度不超过 max_chars 的短内容完全不改变，原样返回；
    - 超长内容保留头部 head_chars 与尾部 tail_chars 字符，
      中间以省略标记代替；省略标记必须说明原始字符数；
    - 只处理纯文本。role / tool_call_id 等消息结构字段由调用方保持不变；
    - 不允许把工具消息删除（本函数只收缩 content）。

    非 str 输入（防御）原样返回。
    """
    if not isinstance(content, str):
        return content

    original_chars = len(content)
    if original_chars <= max_chars:
        return content

    safe_head = max(0, head_chars)
    safe_tail = max(0, tail_chars)
    if safe_head + safe_tail >= original_chars:
        # 防御：极端参数下避免首尾切片重叠，回退为对半保留。
        safe_head = original_chars // 2
        safe_tail = original_chars - safe_head

    marker = _OMITTED_MARKER_TEMPLATE.format(original_chars=original_chars)
    return f"{content[:safe_head]}{marker}{content[-safe_tail:]}"


def _tail_batch_unclosed(messages: list[dict[str, Any]]) -> bool:
    """检测 message_log 尾部是否存在未闭环的 assistant(tool_calls) batch。

    定义：最后一条带 tool_calls 的 assistant 消息之后，
    若存在它声明的 tool_call_id 尚未得到 role=tool 响应，视为未闭环。

    正常情况下 model_node 的 early-return / cursor 逻辑保证进入本模块时
    不存在未闭环 batch；此检查是纯函数层的防御性兜底。
    """
    last_assistant_idx = -1
    for index, message in enumerate(messages):
        if message.get("role") == "assistant" and message.get("tool_calls"):
            last_assistant_idx = index
    if last_assistant_idx < 0:
        return False

    tool_calls = messages[last_assistant_idx].get("tool_calls") or []
    declared_ids = {
        tc.get("id")
        for tc in tool_calls
        if isinstance(tc, dict) and tc.get("id")
    }
    if not declared_ids:
        return False

    responded_ids = {
        message.get("tool_call_id")
        for message in messages[last_assistant_idx + 1:]
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    return bool(declared_ids - responded_ids)


def _build_compressed_message_log(
    message_log: list[dict[str, Any]],
    max_tool_content_chars: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """逐条复制 message_log，压缩超长 role=tool content。

    返回 (新列表, compressed_records)。不修改输入对象。
    compressed_records 每条：role / tool_call_id / original_chars / compressed_chars。
    """
    copied: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []

    for message in message_log:
        if message.get("role") == "tool" and max_tool_content_chars > 0:
            content = message.get("content")
            if isinstance(content, str) and len(content) > max_tool_content_chars:
                new_content = compress_tool_content(
                    content, max_chars=max_tool_content_chars
                )
                copied.append({**message, "content": new_content})
                records.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.get("tool_call_id"),
                        "original_chars": len(content),
                        "compressed_chars": len(new_content),
                    }
                )
                continue

        copied.append(dict(message))

    return copied, records


def build_model_request_messages(
    *,
    system_prompt: str,
    user_message: str,
    message_log: list[dict[str, Any]],
    deferred_feedback: list[dict[str, Any]] | None = None,
    executor_instruction: str,
    max_tool_content_chars: int = DEFAULT_MAX_TOOL_CONTENT_CHARS,
    max_context_tokens: int = 0,
) -> dict[str, Any]:
    """构造发送给 LLM 的 request messages（纯函数，不改任何输入）。

    最终顺序严格保持：

        system_prompt
        → user_message
        → message_log            （仅 role=tool 超长 content 被请求侧压缩）
        → deferred_feedback      （batch 闭环后的待注入反馈）
        → ephemeral executor_instruction

    参数：
    - max_tool_content_chars：role=tool content 超过该字符数才压缩；0 关闭压缩。
    - max_context_tokens：本期只用于统计与 over_budget 判断（>0 时启用判断），
      不真正删除任何历史消息；dropped_messages 本期始终为空。

    返回 {"messages": [...], "context_meta": {...}}。
    """
    protocol_guard_triggered = _tail_batch_unclosed(message_log)

    deferred = [dict(message) for message in (deferred_feedback or [])]

    if protocol_guard_triggered:
        # 防御：尾部存在未闭环 batch，不做任何压缩，原样复制。
        message_log_view = [dict(message) for message in message_log]
        compressed_records: list[dict[str, Any]] = []
    else:
        message_log_view, compressed_records = _build_compressed_message_log(
            message_log,
            max_tool_content_chars,
        )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
        *message_log_view,
        *deferred,
        {"role": "system", "content": executor_instruction},
    ]

    estimated_tokens = estimate_messages_tokens(messages)
    over_budget = max_context_tokens > 0 and estimated_tokens > max_context_tokens

    context_meta: dict[str, Any] = {
        "selected_messages": len(messages),
        "compressed_messages": compressed_records,
        "dropped_messages": [],
        "estimated_tokens": estimated_tokens,
        "max_context_tokens": max_context_tokens,
        "over_budget": over_budget,
        "protocol_guard_triggered": protocol_guard_triggered,
    }

    return {
        "messages": messages,
        "context_meta": context_meta,
    }
