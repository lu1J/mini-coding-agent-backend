"""Day17 Context Builder 单元测试。

覆盖人工架构审查指定的 10 项：
1. 短 tool content 完全不变；
2. 长 tool content 被压缩：role / tool_call_id / 相对顺序不变，
   content 更短且包含带原字符数的省略标记；
3. system_prompt 与 user_message 永远在头部；
4. deferred_feedback 严格位于 message_log → deferred → instruction；
5. executor_instruction 只作为请求内最后一条 system 消息出现
   （持久化职责在 model_node，本模块只保证它永远在尾部）；
6. 多条 tool 消息的相对顺序保持；
7. 完整 assistant(tool_calls) + role=tool 协议对经 builder 后结构不变；
8. 未闭环 batch → protocol_guard_triggered=true 且不做任何压缩；
9. context_meta 字段齐全且数值正确（含 estimate_messages_tokens 复用）；
10. 纯函数：任何输入对象（含嵌套 dict）不被就地修改。
"""

from __future__ import annotations

import copy

from app.agent.context_builder import (
    _OMITTED_MARKER_TEMPLATE,
    build_model_request_messages,
    compress_tool_content,
)
from app.memory.context_manager import estimate_messages_tokens

_SYSTEM = "你是 coding agent。"
_USER = "请完成任务：修改 hello.py。"
_INSTRUCTION = "当前步骤：1/2 修改文件，请调用 edit_file。"


def _assistant_tool_calls(call_ids):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
            }
            for call_id in call_ids
        ],
    }


def _tool_message(tool_call_id, content):
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def _build(**overrides):
    """便捷调用：默认参数 + 覆盖。"""
    defaults = {
        "system_prompt": _SYSTEM,
        "user_message": _USER,
        "message_log": [],
        "deferred_feedback": None,
        "executor_instruction": _INSTRUCTION,
    }
    defaults.update(overrides)
    return build_model_request_messages(**defaults)


# ---------------------------------------------------------------- 1. 短内容不变
def test_short_tool_content_is_unchanged():
    short = "git diff --stat\n 1 file changed, 1 insertion(+)"  # < 4000 chars

    compressed = compress_tool_content(short)

    assert compressed == short

    # 恰好等于阈值也不压缩（<= max_chars 原样返回）。
    boundary = "x" * 4000
    assert compress_tool_content(boundary) == boundary


# -------------------------------------------------- 2. 长内容压缩且结构字段不变
def test_long_tool_content_compressed_keeps_role_and_call_id():
    original = "A" * 5000  # 超过默认 max_chars=4000
    message_log = [
        _assistant_tool_calls(["call_001"]),
        _tool_message("call_001", original),
    ]

    result = _build(message_log=message_log)
    messages = result["messages"]

    assert len(messages) == len(message_log) + 3  # + system / user / instruction

    tool_message = messages[3]  # system, user, assistant, tool
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_001"
    assert len(tool_message["content"]) < len(original)
    assert tool_message["content"].startswith("A" * 1200)
    assert tool_message["content"].endswith("A" * 600)
    # 省略标记内容与实现模板一致，且携带原始字符数。
    assert (
        _OMITTED_MARKER_TEMPLATE.format(original_chars=5000)
        in tool_message["content"]
    )
    # 压缩后仍不超过压缩阈值，避免二次压缩失效。
    assert len(tool_message["content"]) <= 4000

    meta = result["context_meta"]
    assert meta["compressed_messages"] == [
        {
            "role": "tool",
            "tool_call_id": "call_001",
            "original_chars": 5000,
            "compressed_chars": len(tool_message["content"]),
        }
    ]


# ------------------------------------------------- 3. system + user 永远在头部
def test_system_and_user_always_at_head():
    message_log = [
        _assistant_tool_calls(["call_1", "call_2"]),
        _tool_message("call_1", "r1"),
        _tool_message("call_2", "r2"),
    ]
    deferred = [{"role": "system", "content": "[审批后恢复上下文]"}]

    result = _build(
        message_log=message_log,
        deferred_feedback=deferred,
    )
    messages = result["messages"]

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == _SYSTEM
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == _USER


# ------------------------------------- 4. deferred 严格夹在 log 与 instruction 之间
def test_deferred_sits_between_log_and_instruction():
    message_log = [
        _assistant_tool_calls(["call_a", "call_b"]),
        _tool_message("call_a", "ra"),
        _tool_message("call_b", "rb"),
    ]
    deferred = [
        {"role": "system", "content": "[失败自省 1]"},
        {"role": "user", "content": "[审批后恢复上下文]"},
    ]

    result = _build(message_log=message_log, deferred_feedback=deferred)
    messages = result["messages"]

    # 结构：system, user, assistant, tool, tool, deferred_1, deferred_2, instruction
    assert [m["content"] for m in messages] == [
        _SYSTEM,
        _USER,
        "",
        "ra",
        "rb",
        "[失败自省 1]",
        "[审批后恢复上下文]",
        _INSTRUCTION,
    ]

    log_end = 2 + len(message_log)  # system + user + message_log 全部排完
    assert [m["content"] for m in messages[log_end:-1]] == [
        "[失败自省 1]",
        "[审批后恢复上下文]",
    ]
    assert messages[-1]["role"] == "system"
    assert messages[-1]["content"] == _INSTRUCTION


# --------------------------- 5. executor_instruction 只出现一次且在请求尾部
def test_executor_instruction_is_request_tail_only():
    message_log = [
        _assistant_tool_calls(["call_x"]),
        _tool_message("call_x", "ok"),
    ]

    result = _build(message_log=message_log)
    messages = result["messages"]

    assert messages[-1] == {"role": "system", "content": _INSTRUCTION}
    # 请求内除尾部外不得出现第二条内容为 instruction 的消息
    # （防止它与历史中的 system 指令重复/错位注入）。
    instruction_count = sum(
        1 for m in messages if m.get("content") == _INSTRUCTION
    )
    assert instruction_count == 1


# ----------------------------------------- 6. 多条 tool 消息顺序保持（含超长）
def test_multiple_tool_messages_order_preserved():
    long_b = "B" * 5000
    long_d = "D" * 5000
    message_log = [
        _assistant_tool_calls(["call_a", "call_b", "call_c", "call_d"]),
        _tool_message("call_a", "short a"),
        _tool_message("call_b", long_b),
        _tool_message("call_c", "short c"),
        _tool_message("call_d", long_d),
    ]

    result = _build(message_log=message_log)
    messages = result["messages"]
    tool_messages = [m for m in messages if m.get("role") == "tool"]

    assert [m["tool_call_id"] for m in tool_messages] == [
        "call_a",
        "call_b",
        "call_c",
        "call_d",
    ]
    assert tool_messages[0]["content"] == "short a"
    assert tool_messages[2]["content"] == "short c"
    assert "B" * 5000 not in tool_messages[1]["content"]
    assert "D" * 5000 not in tool_messages[3]["content"]
    assert len(tool_messages[1]["content"]) < 4000
    assert len(tool_messages[3]["content"]) < 4000

    meta = result["context_meta"]
    assert len(meta["compressed_messages"]) == 2
    assert [r["tool_call_id"] for r in meta["compressed_messages"]] == [
        "call_b",
        "call_d",
    ]


# ----------------------------------- 7. 完整协议对经 builder 后结构不变
def test_closed_batch_protocol_structure_unchanged():
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "run_command",
                "arguments": '{"command": "pytest"}',
            },
        },
        {
            "id": "call_2",
            "type": "function",
            "function": {
                "name": "read_file",
                "arguments": '{"path": "hello.py"}',
            },
        },
    ]
    long_result = "x" * 5000
    message_log = [
        {"role": "assistant", "content": "", "tool_calls": tool_calls},
        _tool_message("call_1", long_result),
        _tool_message("call_2", "ok"),
    ]

    result = _build(message_log=message_log)
    messages = result["messages"]

    # assistant(tool_calls) 与其 role=tool 响应作为整体排在 user 之后。
    assistant_message = messages[2]
    assert assistant_message["role"] == "assistant"
    assert assistant_message["tool_calls"] == tool_calls  # 结构深等，不受压缩影响
    assert messages[3]["role"] == "tool"
    assert messages[3]["tool_call_id"] == "call_1"
    assert messages[4]["role"] == "tool"
    assert messages[4]["tool_call_id"] == "call_2"

    # 压缩只改动 content 字段，不改动 assistant 的声明。
    assert messages[3]["content"] != long_result
    assert len(messages[3]["content"]) < 4000
    # 协议连续性：assistant 之后紧跟两条 role=tool，没有其他角色插入。
    assert [m["role"] for m in messages[2:5]] == ["assistant", "tool", "tool"]


# ------------------------- 8. 未闭环 batch → protocol_guard_triggered 且无压缩
def test_unclosed_batch_triggers_protocol_guard_no_compression():
    long_result = "y" * 5000  # 本应被压缩
    message_log = [
        _assistant_tool_calls(["call_1", "call_2"]),
        _tool_message("call_1", long_result),
        # call_2 没有 role=tool 响应 → 未闭环。
    ]

    result = _build(message_log=message_log)
    messages = result["messages"]
    meta = result["context_meta"]

    assert meta["protocol_guard_triggered"] is True
    # 防御触发时不做任何压缩：原文完整保留。
    tool_message = messages[3]
    assert tool_message["content"] == long_result
    assert meta["compressed_messages"] == []

    # 正常闭环的对照：guard 不触发且压缩生效。
    closed_log = [
        _assistant_tool_calls(["call_1", "call_2"]),
        _tool_message("call_1", long_result),
        _tool_message("call_2", "ok"),
    ]
    closed_meta = _build(message_log=closed_log)["context_meta"]
    assert closed_meta["protocol_guard_triggered"] is False
    assert len(closed_meta["compressed_messages"]) == 1


# ------------------------------------------ 9. context_meta 字段齐全且正确
def test_context_meta_fields_are_complete_and_correct():
    long_result = "z" * 5000
    message_log = [
        _assistant_tool_calls(["call_1"]),
        _tool_message("call_1", long_result),
    ]
    max_context_tokens = 16000

    result = _build(
        message_log=message_log,
        max_context_tokens=max_context_tokens,
    )
    messages = result["messages"]
    meta = result["context_meta"]

    # 7 个字段全部存在。
    assert set(meta) == {
        "selected_messages",
        "compressed_messages",
        "dropped_messages",
        "estimated_tokens",
        "max_context_tokens",
        "over_budget",
        "protocol_guard_triggered",
    }

    assert meta["selected_messages"] == len(messages)
    assert len(meta["compressed_messages"]) == 1
    # L2 message dropping 本期关闭：dropped 恒为空。
    assert meta["dropped_messages"] == []
    # token 估算复用 app/memory/context_manager.estimate_messages_tokens。
    assert meta["estimated_tokens"] == estimate_messages_tokens(messages)
    assert meta["max_context_tokens"] == max_context_tokens
    # 压缩后约数百 token，远小于 16000 预算。
    assert meta["over_budget"] is False

    # over_budget 语义：仅当 max_context_tokens>0 且估算超出时才为 True。
    tiny_budget = 10
    assert (
        _build(message_log=message_log, max_context_tokens=tiny_budget)["context_meta"][
            "over_budget"
        ]
        is True
    )
    # max_context_tokens=0（未启用预算）时恒为 False。
    assert _build(message_log=message_log, max_context_tokens=0)["context_meta"][
        "over_budget"
    ] is False

    # protocol_guard_triggered 正常路径为 False。
    assert meta["protocol_guard_triggered"] is False


# -------------------------------------- 10. 纯函数：任何输入对象不被修改
def test_builder_does_not_mutate_inputs():
    long_result = "w" * 5000
    message_log = [
        _assistant_tool_calls(["call_1"]),
        _tool_message("call_1", long_result),
    ]
    deferred = [{"role": "system", "content": "[失败自省]"}]

    log_before = copy.deepcopy(message_log)
    deferred_before = copy.deepcopy(deferred)

    result = _build(
        message_log=message_log,
        deferred_feedback=deferred,
    )

    assert message_log == log_before
    assert deferred == deferred_before
    # 超长原文仍完整存在于输入中（State 原文不受压缩影响）。
    assert message_log[1]["content"] == long_result

    # 输出中的消息是副本：即使调用方后续修改输出，也不影响输入对象。
    result_messages = result["messages"]
    tool_index = next(
        i for i, m in enumerate(result_messages) if m.get("role") == "tool"
    )
    assert result_messages[tool_index] is not message_log[1]
    # 未压缩的 assistant(tool_calls) 消息同样是副本而非输入原对象。
    assistant_index = next(
        i for i, m in enumerate(result_messages) if m.get("role") == "assistant"
    )
    assert result_messages[assistant_index] is not message_log[0]
