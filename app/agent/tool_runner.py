import json
from typing import Any

from app.llm.deepseek_client import llm
from app.tools.time_tools import TIME_TOOLS, AVAILABLE_TIME_TOOLS


def run_time_tool_agent(user_message: str) -> dict[str, Any]:
    """
    一个最小版 Tool Calling Agent。

    它能完成：
    1. 把用户问题发给模型
    2. 让模型判断是否调用时间工具
    3. 如果模型请求工具，后端执行工具
    4. 把工具结果返回给模型
    5. 让模型生成最终回答

    当前版本只支持一次工具调用。
    后面我们会升级成 ReAct 循环。
    """

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个会使用工具的 AI 助手。"
                "当用户询问当前时间、现在几点、某个时区时间时，必须调用工具，不要凭空猜测。"
            )
        },
        {
            "role": "user",
            "content": user_message
        }
    ]

    steps = []

    # 第一次调用模型：让模型决定是否需要工具
    first_response = llm.client.chat.completions.create(
        model=llm.model,
        messages=messages,
        tools=TIME_TOOLS,
        tool_choice="auto",
    )

    assistant_message = first_response.choices[0].message

    # 如果模型没有调用工具，直接返回模型回答
    if not assistant_message.tool_calls:
        return {
            "answer": assistant_message.content,
            "steps": [
                {
                    "type": "no_tool_call",
                    "message": "模型没有调用工具，直接回答。"
                }
            ]
        }

    # 把模型的 tool_calls 消息加入 messages
    assistant_tool_calls = []

    for tool_call in assistant_message.tool_calls:
        assistant_tool_calls.append({
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments,
            }
        })

    messages.append({
        "role": "assistant",
        "content": assistant_message.content or "",
        "tool_calls": assistant_tool_calls,
    })

    # 执行模型请求的工具
    for tool_call in assistant_message.tool_calls:
        tool_name = tool_call.function.name
        tool_args_json = tool_call.function.arguments

        try:
            tool_args = json.loads(tool_args_json)
        except json.JSONDecodeError:
            tool_args = {}

        tool_func = AVAILABLE_TIME_TOOLS.get(tool_name)

        if not tool_func:
            tool_result = f"错误：没有找到工具 {tool_name}"
        else:
            try:
                tool_result = tool_func(**tool_args)
            except Exception as e:
                tool_result = f"工具执行失败：{str(e)}"

        steps.append({
            "type": "tool_call",
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_result": tool_result,
        })

        # 把工具结果放回 messages
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": str(tool_result),
        })

    # 第二次调用模型：基于工具结果生成最终回答
    final_response = llm.client.chat.completions.create(
        model=llm.model,
        messages=messages,
    )

    final_answer = final_response.choices[0].message.content

    return {
        "answer": final_answer,
        "steps": steps,
    }