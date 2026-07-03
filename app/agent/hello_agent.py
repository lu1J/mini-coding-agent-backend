import json
from typing import Any

from app.llm.deepseek_client import llm
from app.tools.time_tools import TIME_TOOLS, AVAILABLE_TIME_TOOLS
from app.tools.text_tools import TEXT_TOOLS, AVAILABLE_TEXT_TOOLS

from app.tools.file_tools import FILE_TOOLS, AVAILABLE_FILE_TOOLS


ALL_TOOLS = TIME_TOOLS + TEXT_TOOLS + FILE_TOOLS

AVAILABLE_TOOLS = {
    **AVAILABLE_TIME_TOOLS,
    **AVAILABLE_TEXT_TOOLS,
    **AVAILABLE_FILE_TOOLS,
}


def build_clean_tool_calls(tool_calls) -> list[dict[str, Any]]:
    """
    把模型返回的 tool_calls 转成干净的 dict。

    为什么需要这个函数？
    因为模型返回的 tool_call 对象里可能有一些额外字段，
    直接塞回 messages 可能导致格式不兼容。
    """
    clean_tool_calls = []

    for tool_call in tool_calls:
        clean_tool_calls.append({
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments,
            }
        })

    return clean_tool_calls


def execute_tool(tool_call) -> dict[str, Any]:
    """
    执行单个工具调用。

    输入：
    - 模型返回的 tool_call

    输出：
    - 工具名
    - 工具参数
    - 工具执行结果
    """
    tool_name = tool_call.function.name
    tool_args_json = tool_call.function.arguments

    try:
        tool_args = json.loads(tool_args_json)
    except json.JSONDecodeError:
        tool_args = {}

    tool_func = AVAILABLE_TOOLS.get(tool_name)

    if not tool_func:
        tool_result = f"错误：没有找到工具 {tool_name}"
    else:
        try:
            tool_result = tool_func(**tool_args)
        except Exception as e:
            tool_result = f"工具执行失败：{str(e)}"

    return {
        "tool_call_id": tool_call.id,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "tool_result": str(tool_result),
    }


def run_hello_agent(user_message: str, max_steps: int = 5) -> dict[str, Any]:
    """
    HelloAgent：最小版 ReAct / Tool Calling 循环。

    它能完成：
    1. 接收用户任务
    2. 调用模型判断是否需要工具
    3. 如果模型返回 tool_calls，就执行工具
    4. 把工具结果放回 messages
    5. 再次调用模型
    6. 循环直到模型给出最终回答，或者达到最大步数

    参数：
    - user_message：用户输入的任务
    - max_steps：最多循环多少轮，防止 Agent 死循环
    """

    messages = [
        {
            "role": "system",
            "content": (
                "你是 HelloAgent，一个会使用工具解决问题的 AI 助手。"
                "当用户询问当前时间、现在几点、某个时区时间时，必须调用 get_current_time 工具。"
                "当用户要求统计文本字符数、字数、文本长度时，必须调用 count_text_chars 工具。"
                "当用户要求查看项目结构、列出文件、查看目录时，必须调用 list_files 工具。"
                "当用户要求读取文件内容、查看代码、查看 README 时，必须调用 read_file 工具。"
                "当用户要求查找函数、接口、类、变量、关键词出现位置时，必须先调用 search_code 工具。"
                "当用户明确要求修改文件、修改代码、替换内容时，可以调用 edit_file 工具。"
                "在调用 edit_file 之前，必须先通过 read_file 或 search_code 确认原始内容。"
                "修改后要根据工具返回的 diff 总结改动。"
                "当用户要求运行检查、执行测试、验证代码是否有语法错误时，可以调用 run_command 工具。"
                "调用 run_command 时，只能使用安全白名单命令，例如 python -m py_compile 或 pytest。"
                "修改代码后，如果用户要求验证，应该运行合适的检查命令。"
                "当用户要求查看某个文件改了什么、查看单文件 diff 时，可以调用 get_file_diff 工具。"
                "当用户要求查看整个工作区改动、全部 diff、当前修改摘要时，可以调用 get_workspace_diff 工具。"
                "在完成代码修改后，如果用户要求审查改动，应该调用 diff 工具。"
                "你只能通过工具查看 workspace 工作区里的文件，不要假设文件内容。"
                "如果任务需要多个工具，可以按需要多次调用工具。"
                "工具返回结果后，你要基于工具结果继续思考，直到给出最终答案。"
)
        },
        {
            "role": "user",
            "content": user_message
        }
    ]

    steps = []

    for step_index in range(1, max_steps + 1):
        response = llm.client.chat.completions.create(
            model=llm.model,
            messages=messages,
            tools=ALL_TOOLS,
            tool_choice="auto",
        )

        assistant_message = response.choices[0].message

        # 情况 1：模型没有请求工具，说明它认为可以最终回答了
        if not assistant_message.tool_calls:
            final_answer = assistant_message.content or ""

            steps.append({
                "step": step_index,
                "type": "final_answer",
                "content": final_answer,
            })

            return {
                "status": "finished",
                "answer": final_answer,
                "steps": steps,
            }

        # 情况 2：模型请求调用工具
        clean_tool_calls = build_clean_tool_calls(assistant_message.tool_calls)

        messages.append({
            "role": "assistant",
            "content": assistant_message.content or "",
            "tool_calls": clean_tool_calls,
        })

        for tool_call in assistant_message.tool_calls:
            tool_execution = execute_tool(tool_call)

            steps.append({
                "step": step_index,
                "type": "tool_call",
                "tool_name": tool_execution["tool_name"],
                "tool_args": tool_execution["tool_args"],
                "tool_result": tool_execution["tool_result"],
            })

            # 把工具执行结果放回 messages，作为模型下一轮的观察结果
            messages.append({
                "role": "tool",
                "tool_call_id": tool_execution["tool_call_id"],
                "content": tool_execution["tool_result"],
            })

    # 如果达到最大步数还没有结束，强制停止
    return {
        "status": "max_steps_reached",
        "answer": "Agent 达到最大执行步数，已停止。请简化任务或增加 max_steps。",
        "steps": steps,
    }