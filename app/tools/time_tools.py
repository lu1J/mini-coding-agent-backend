from datetime import datetime, timezone as dt_timezone, timedelta
from zoneinfo import ZoneInfo


def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """
    获取指定时区的当前时间。

    注意：
    这个函数是真正由 Python 后端执行的。
    模型本身不会执行这个函数。
    """
    try:
        now = datetime.now(ZoneInfo(timezone))
        return now.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        # Windows 环境兜底：北京时间 UTC+8
        if timezone in ["Asia/Shanghai", "Asia/Chongqing", "Asia/Beijing"]:
            now = datetime.now(dt_timezone(timedelta(hours=8)))
            return now.strftime("%Y-%m-%d %H:%M:%S")

        return f"不支持的时区：{timezone}"


TIME_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取指定时区的当前时间。当用户询问现在几点、当前时间、某个时区时间时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA 时区名称，例如 Asia/Shanghai、America/New_York、Europe/London。"
                    }
                },
                "required": ["timezone"]
            }
        }
    }
]


AVAILABLE_TIME_TOOLS = {
    "get_current_time": get_current_time
}