from __future__ import annotations

import argparse
import os
from typing import Callable

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain.agents import create_agent
from langchain.messages import HumanMessage, ToolMessage
from langchain.tools import tool
from langchain_deepseek import ChatDeepSeek
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver


load_dotenv()


# ============================================================
# 1. Structured Output Schema
# ============================================================


class ConceptCard(BaseModel):
    """用于演示 LangChain Structured Output。"""

    name: str = Field(description="概念名称")

    beginner_explanation: str = Field(
        description="面向初学者的一句话解释"
    )

    interview_answer: str = Field(
        description="面试时可以使用的简洁回答"
    )

    keywords: list[str] = Field(
        description="与该概念相关的核心关键词"
    )


# ============================================================
# 2. Tools
# ============================================================


@tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers and return the product."""
    return a * b


@tool
def text_length(text: str) -> int:
    """Return the number of characters in the given text."""
    return len(text)


# ============================================================
# 3. Model Factory
# ============================================================


def build_model() -> ChatDeepSeek:
    """
    构建 Day16 使用的 LangChain DeepSeek Model。

    优先读取：
        DEEPSEEK_API_KEY
        DEEPSEEK_API_BASE

    如果项目原来使用 OpenAI-compatible 环境变量，
    同时存在 OPENAI_API_KEY + OPENAI_BASE_URL，
    也允许复用。

    DAY16_MODEL 可以覆盖默认模型。
    """

    deepseek_key = os.getenv("DEEPSEEK_API_KEY")

    deepseek_base = (
        os.getenv("DEEPSEEK_API_BASE")
        or os.getenv("DEEPSEEK_BASE_URL")
    )

    compat_key = os.getenv("OPENAI_API_KEY")

    compat_base = (
        os.getenv("OPENAI_BASE_URL")
        or os.getenv("OPENAI_API_BASE")
    )

    if deepseek_key:
        api_key = deepseek_key
        api_base = deepseek_base

    elif compat_key and compat_base:
        api_key = compat_key
        api_base = compat_base

    else:
        raise RuntimeError(
            "没有找到可用的 DeepSeek API 配置。\n"
            "请确认 .env 中存在 DEEPSEEK_API_KEY，"
            "或者 OPENAI_API_KEY + OPENAI_BASE_URL。\n"
            "不要把 API Key 发到聊天里。"
        )

    model_name = os.getenv(
        "DAY16_MODEL",
        "deepseek-chat",
    )

    kwargs = {
        "model": model_name,
        "api_key": api_key,
        "temperature": 0,
        "max_retries": 2,
    }

    if api_base:
        kwargs["api_base"] = api_base

    return ChatDeepSeek(**kwargs)


# ============================================================
# 4. Runnable / LCEL
# ============================================================


def build_offline_runnable():
    """
    不访问任何 LLM。

    演示：

        Runnable
        |
        RunnableParallel

    输入：
        "  hello agent  "

    输出：
        {
            "normalized": "hello agent",
            "uppercase": "HELLO AGENT",
            "length": 11
        }
    """

    normalize = RunnableLambda(
        lambda text: text.strip()
    )

    return normalize | {
        "normalized": RunnableLambda(
            lambda text: text
        ),
        "uppercase": RunnableLambda(
            lambda text: text.upper()
        ),
        "length": RunnableLambda(
            lambda text: len(text)
        ),
    }


def demo_runnable() -> None:
    print("\n=== Runnable / LCEL 基础 Demo ===")

    chain = build_offline_runnable()

    result = chain.invoke(
        "  hello agent  "
    )

    print("invoke result:")
    print(result)

    batch_result = chain.batch(
        [
            "  langchain  ",
            "  langgraph  ",
            "  coding agent  ",
        ]
    )

    print("\nbatch result:")

    for item in batch_result:
        print(item)


# ============================================================
# 5. Prompt | Model | Parser
# ============================================================


def demo_lcel() -> None:
    print(
        "\n=== Prompt | Model | Parser Demo ==="
    )

    model = build_model()

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是一名 Agent 工程导师。"
                "请使用简单准确的中文解释技术概念。",
            ),
            (
                "human",
                "请用两句话解释：{topic}",
            ),
        ]
    )

    chain = (
        prompt
        | model
        | StrOutputParser()
    )

    result = chain.invoke(
        {
            "topic": "LangChain Runnable"
        }
    )

    print(result)


# ============================================================
# 6. Structured Output
# ============================================================


def demo_structured_output() -> None:
    print(
        "\n=== Structured Output Demo ==="
    )

    model = build_model()

    structured_model = (
        model.with_structured_output(
            ConceptCard
        )
    )

    result = structured_model.invoke(
        [
            (
                "system",
                "你是一名 Agent 工程导师。",
            ),
            (
                "human",
                "解释 Tool Call Batch Barrier。"
                "请同时给出小白解释、面试表达和关键词。",
            ),
        ]
    )

    print("Python type:")
    print(type(result))

    print("\nStructured result:")
    print(
        result.model_dump_json(
            indent=2
        )
    )


# ============================================================
# 7. 手动 Tool Calling Protocol
# ============================================================


def demo_tool_protocol() -> None:
    """
    这个 Demo 非常重要。

    它手动展示：

        HumanMessage
            ↓
        AIMessage(tool_calls)
            ↓
        ToolMessage
            ↓
        AIMessage(final)

    对应 Day15 Bug2。
    """

    print(
        "\n=== Manual Tool Calling Protocol Demo ==="
    )

    model = build_model()

    model_with_tools = model.bind_tools(
        [multiply],
        parallel_tool_calls=False,
    )

    user_message = HumanMessage(
        content=(
            "请使用 multiply 工具计算 17 * 23。"
            "工具返回以后，再用一句中文告诉我最终结果。"
        )
    )

    first_ai_message = (
        model_with_tools.invoke(
            [user_message]
        )
    )

    print("\n1. AIMessage.tool_calls:")

    print(
        first_ai_message.tool_calls
    )

    if not first_ai_message.tool_calls:
        print(
            "模型本轮没有调用 Tool，"
            "请重新执行一次 Demo。"
        )
        return

    tool_call = (
        first_ai_message.tool_calls[0]
    )

    if tool_call["name"] != "multiply":
        raise RuntimeError(
            "模型请求了未知 Tool："
            f"{tool_call['name']}"
        )

    tool_result = multiply.invoke(
        tool_call["args"]
    )

    print("\n2. Runtime 真正执行 Tool:")

    print(
        f"multiply result = {tool_result}"
    )

    tool_message = ToolMessage(
        content=str(tool_result),
        tool_call_id=tool_call["id"],
    )

    conversation = [
        user_message,
        first_ai_message,
        tool_message,
    ]

    print(
        "\n3. Conversation Protocol:"
    )

    for message in conversation:
        print(
            type(message).__name__,
            getattr(
                message,
                "tool_call_id",
                "",
            ),
        )

    final_ai_message = (
        model_with_tools.invoke(
            conversation
        )
    )

    print("\n4. Final AIMessage:")

    print(final_ai_message.content)


# ============================================================
# 8. create_agent / ReAct-style Agent
# ============================================================


def demo_agent() -> None:
    print(
        "\n=== LangChain create_agent Demo ==="
    )

    model = build_model()

    agent = create_agent(
        model=model,
        tools=[
            multiply,
            text_length,
        ],
        system_prompt=(
            "你是一个简单的工具调用 Agent。"
            "数学计算必须使用 multiply；"
            "字符串长度必须使用 text_length。"
            "完成工具调用后给出简洁中文总结。"
        ),
    )

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "请计算 17 * 23，"
                        "再计算字符串 "
                        "'LangChain Day16' "
                        "有多少个字符，"
                        "最后一起告诉我。"
                    ),
                }
            ]
        }
    )

    print("\nAgent message timeline:")

    for message in result["messages"]:
        message_type = (
            type(message).__name__
        )

        print(
            f"- {message_type}"
        )

        tool_calls = getattr(
            message,
            "tool_calls",
            None,
        )

        if tool_calls:
            print(
                f"  tool_calls={tool_calls}"
            )

        tool_call_id = getattr(
            message,
            "tool_call_id",
            None,
        )

        if tool_call_id:
            print(
                f"  tool_call_id={tool_call_id}"
            )

    print("\nFinal Answer:")

    print(
        result["messages"][-1].content
    )


# ============================================================
# 9. Short-term Memory
# ============================================================


def demo_memory() -> None:
    print(
        "\n=== Short-term Memory Demo ==="
    )

    model = build_model()

    checkpointer = InMemorySaver()

    agent = create_agent(
        model=model,
        tools=[],
        system_prompt=(
            "你是一个记忆测试助手。"
            "请准确记住同一 thread 中"
            "用户之前告诉你的信息。"
        ),
        checkpointer=checkpointer,
    )

    config = {
        "configurable": {
            "thread_id": (
                "day16-memory-demo"
            )
        }
    }

    agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "请记住这个数字：73。"
                        "暂时只回复：已记住。"
                    ),
                }
            ]
        },
        config,
    )

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "我刚才让你记住的"
                        "数字是多少？"
                    ),
                }
            ]
        },
        config,
    )

    print(
        result["messages"][-1].content
    )


# ============================================================
# 10. CLI
# ============================================================


DEMOS: dict[
    str,
    Callable[[], None],
] = {
    "runnable": demo_runnable,
    "lcel": demo_lcel,
    "structured": demo_structured_output,
    "tool": demo_tool_protocol,
    "agent": demo_agent,
    "memory": demo_memory,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Mini Coding Agent Backend "
            "Day16 LangChain Learning Lab"
        )
    )

    parser.add_argument(
        "demo",
        choices=DEMOS.keys(),
        help="选择一个 Day16 Demo",
    )

    args = parser.parse_args()

    DEMOS[args.demo]()


if __name__ == "__main__":
    main()