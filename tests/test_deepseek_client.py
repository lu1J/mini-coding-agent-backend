from types import SimpleNamespace

import pytest

from app.llm.deepseek_client import DeepSeekClient


def test_deepseek_client_can_be_created_without_api_key():
    """
    没有 API Key 时，应该允许创建配置对象。

    只要还没有真正调用模型，
    就不应该在初始化阶段报错。
    """

    client = DeepSeekClient(
        api_key="",
        model="test-model",
    )

    assert client.api_key == ""
    assert client.model == "test-model"


def test_deepseek_client_checks_api_key_on_first_model_call():
    """
    真正调用模型时，如果没有 API Key，
    应该返回清楚的配置错误。
    """

    client = DeepSeekClient(
        api_key="",
        model="test-model",
    )

    with pytest.raises(
        ValueError,
        match="没有找到 DEEPSEEK_API_KEY",
    ):
        client.chat(
            [
                {
                    "role": "user",
                    "content": "hello",
                }
            ]
        )


def test_deepseek_client_supports_injected_fake_client():
    """
    测试可以注入假的模型客户端，
    不需要真实 API Key，也不会发起网络请求。
    """

    captured = {}

    def fake_create(*args, **kwargs):
        captured["kwargs"] = kwargs

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="fake answer",
                    )
                )
            ]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=fake_create,
            )
        )
    )

    client = DeepSeekClient(
        api_key="",
        model="fake-model",
        client=fake_client,
    )

    answer = client.chat(
        [
            {
                "role": "user",
                "content": "hello",
            }
        ],
        max_tokens=123,
    )

    assert answer == "fake answer"

    assert captured["kwargs"]["model"] == "fake-model"
    assert captured["kwargs"]["max_tokens"] == 123
    assert captured["kwargs"]["messages"][-1]["content"] == "hello"