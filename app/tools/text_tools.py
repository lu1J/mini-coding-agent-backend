def count_text_chars(text: str) -> str:
    """
    统计文本的字符数量。

    注意：
    这是一个真正由 Python 后端执行的工具函数。
    模型不会自己执行这个函数，只会请求后端调用它。
    """
    length = len(text)
    return f"文本内容：{text}，字符数量：{length}"


TEXT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "count_text_chars",
            "description": "统计一段文本的字符数量。当用户要求统计字数、字符数、文本长度时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "需要统计字符数量的文本。"
                    }
                },
                "required": ["text"]
            }
        }
    }
]


AVAILABLE_TEXT_TOOLS = {
    "count_text_chars": count_text_chars
}