import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


class _LazyCompletions:
    """
    延迟代理 OpenAI Client 的 completions.create()。

    只有真正调用 create() 时，
    才会让 DeepSeekClient 创建真实 OpenAI Client。
    """

    def __init__(self, owner: "DeepSeekClient"):
        self._owner = owner

    def create(self, *args, **kwargs):
        real_client = self._owner._get_openai_client()

        return real_client.chat.completions.create(
            *args,
            **kwargs,
        )


class _LazyChat:
    """
    保持 client.chat.completions 的访问结构。
    """

    def __init__(self, owner: "DeepSeekClient"):
        self.completions = _LazyCompletions(owner)


class _LazyOpenAIClient:
    """
    一个轻量代理对象。

    作用是保持现有代码仍然可以写：

        llm.client.chat.completions.create(...)

    但模块导入时不会创建真实 OpenAI Client。
    """

    def __init__(self, owner: "DeepSeekClient"):
        self.chat = _LazyChat(owner)


class DeepSeekClient:
    """
    DeepSeek 模型客户端。

    主要职责：

    1. 读取模型配置；
    2. 延迟创建 OpenAI-compatible Client；
    3. 提供普通聊天 chat()；
    4. 提供流式聊天 stream_chat()；
    5. 支持测试时注入假的模型客户端。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ):
        """
        创建 DeepSeekClient 配置对象。

        注意：
        这里只读取配置，不立即要求 API Key，
        也不立即创建真实 OpenAI Client。
        """

        self.api_key = (
            api_key
            if api_key is not None
            else os.getenv("DEEPSEEK_API_KEY")
        )

        self.base_url = (
            base_url
            or os.getenv(
                "DEEPSEEK_BASE_URL",
                "https://api.deepseek.com",
            )
        )

        self.model = (
            model
            or os.getenv(
                "DEEPSEEK_MODEL",
                "deepseek-v4-flash",
            )
        )

        # 如果测试传入了 fake client，就直接保存。
        # 正式运行时这里通常是 None。
        self._openai_client = client

        if client is not None:
            self.client = client
        else:
            self.client = _LazyOpenAIClient(self)

    def _get_openai_client(self) -> Any:
        """
        获取真实 OpenAI Client。

        第一次调用模型时才创建；
        创建成功后保存，后续重复使用。
        """

        if self._openai_client is not None:
            return self._openai_client

        if not self.api_key:
            raise ValueError(
                "没有找到 DEEPSEEK_API_KEY，请检查 .env 文件"
            )

        self._openai_client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

        return self._openai_client

    def chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 800,
    ) -> str:
        """
        普通聊天：一次性返回完整回答。
        """

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
        )

        return response.choices[0].message.content or ""

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 800,
    ):
        """
        流式聊天：逐段返回模型生成的内容。
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


# 这里只创建轻量配置对象。
# 不会在模块导入时创建真实 OpenAI Client。
llm = DeepSeekClient()