import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class DeepSeekClient:
    """
    DeepSeek 模型客户端。

    作用：
    1. 统一读取 .env 配置
    2. 统一创建 OpenAI-compatible 客户端
    3. 提供普通聊天 chat()
    4. 提供流式聊天 stream_chat()

    后面如果你想把 DeepSeek 换成 Claude、Qwen、OpenAI，
    尽量只改这一层，不要到处改业务代码。
    """

    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

        if not self.api_key:
            raise ValueError("没有找到 DEEPSEEK_API_KEY，请检查 .env 文件")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    def chat(self, messages: list[dict[str, Any]], max_tokens: int = 800) -> str:
        """
        普通聊天：一次性返回完整回答。
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
        )

        return response.choices[0].message.content

    def stream_chat(self, messages: list[dict[str, Any]], max_tokens: int = 800):
        """
        流式聊天：一段一段返回模型生成的内容。
        """
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
        )

        for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)

            if content:
                yield content


llm = DeepSeekClient()