import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path("workspace").resolve()
CONVERSATION_DIR = WORKSPACE_ROOT / ".conversations"


def utc_now_iso() -> str:
    """
    返回 UTC ISO 时间字符串。
    """
    return datetime.now(timezone.utc).isoformat()


def ensure_conversation_dir() -> None:
    """
    确保会话存储目录存在。
    """
    CONVERSATION_DIR.mkdir(parents=True, exist_ok=True)


def generate_conversation_id() -> str:
    """
    生成一个会话 ID。
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    return f"conv_{timestamp}_{short_id}"


def validate_conversation_id(conversation_id: str) -> str:
    """
    校验 conversation_id，防止路径穿越。
    """
    if not conversation_id:
        raise ValueError("conversation_id 不能为空")

    allowed_prefix = "conv_"

    if not conversation_id.startswith(allowed_prefix):
        raise ValueError("conversation_id 格式不正确")

    if "/" in conversation_id or "\\" in conversation_id or ".." in conversation_id:
        raise ValueError("conversation_id 包含非法字符")

    return conversation_id


def get_conversation_path(conversation_id: str) -> Path:
    """
    获取某个会话对应的 JSON 文件路径。
    """
    ensure_conversation_dir()
    safe_id = validate_conversation_id(conversation_id)
    return CONVERSATION_DIR / f"{safe_id}.json"


def create_conversation(
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    创建一个新会话。
    """
    ensure_conversation_dir()

    conversation_id = generate_conversation_id()
    now = utc_now_iso()

    conversation = {
        "conversation_id": conversation_id,
        "title": title or "New Conversation",
        "created_at": now,
        "updated_at": now,
        "metadata": metadata or {},
        "messages": [],
    }

    save_conversation(conversation)
    return conversation


def save_conversation(conversation: dict[str, Any]) -> None:
    """
    保存会话到本地 JSON 文件。
    """
    conversation_id = validate_conversation_id(conversation["conversation_id"])
    path = get_conversation_path(conversation_id)

    path.write_text(
        json.dumps(conversation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_conversation(conversation_id: str) -> dict[str, Any]:
    """
    读取会话详情。
    """
    path = get_conversation_path(conversation_id)

    if not path.exists():
        raise FileNotFoundError(f"会话不存在：{conversation_id}")

    return json.loads(path.read_text(encoding="utf-8"))


def append_message(
    conversation_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    向会话中追加一条消息。
    """
    if role not in {"system", "user", "assistant", "tool"}:
        raise ValueError(f"不支持的消息角色：{role}")

    if not content:
        raise ValueError("消息内容不能为空")

    conversation = read_conversation(conversation_id)

    message = {
        "role": role,
        "content": content,
        "created_at": utc_now_iso(),
        "metadata": metadata or {},
    }

    conversation["messages"].append(message)
    conversation["updated_at"] = utc_now_iso()

    save_conversation(conversation)

    return message


def create_or_read_conversation(
    conversation_id: str | None = None,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    如果 conversation_id 存在，则读取会话；
    如果不存在，则创建新会话。
    """
    if conversation_id:
        return read_conversation(conversation_id)

    return create_conversation(title=title, metadata=metadata)


def list_conversations(limit: int = 20) -> list[dict[str, Any]]:
    """
    列出最近的会话摘要。
    """
    ensure_conversation_dir()

    items: list[dict[str, Any]] = []

    for path in CONVERSATION_DIR.glob("conv_*.json"):
        try:
            conversation = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        messages = conversation.get("messages", [])

        items.append({
            "conversation_id": conversation.get("conversation_id"),
            "title": conversation.get("title", "New Conversation"),
            "created_at": conversation.get("created_at"),
            "updated_at": conversation.get("updated_at"),
            "message_count": len(messages),
        })

    items.sort(
        key=lambda item: item.get("updated_at") or "",
        reverse=True,
    )

    return items[:limit]


def build_messages_for_llm(
    conversation_id: str,
    system_message: str | None = None,
    max_messages: int = 20,
) -> list[dict[str, str]]:
    """
    将本地会话记录转换成 LLM messages 格式。

    当前版本先做简单滑动窗口：
    只取最近 max_messages 条消息。

    后续 v0.2.x 会加入摘要压缩。
    """
    conversation = read_conversation(conversation_id)

    raw_messages = conversation.get("messages", [])

    recent_messages = raw_messages[-max_messages:]

    messages: list[dict[str, str]] = []

    if system_message:
        messages.append({
            "role": "system",
            "content": system_message,
        })

    for item in recent_messages:
        role = item.get("role")
        content = item.get("content")

        if role in {"user", "assistant", "system", "tool"} and content:
            messages.append({
                "role": role,
                "content": content,
            })

    return messages