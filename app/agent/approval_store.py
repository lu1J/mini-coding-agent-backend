import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path("workspace").resolve()
PENDING_DIR = WORKSPACE_ROOT / ".agent_pending"


def now_text() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def create_approval_id() -> str:
    return f"approval_{now_text()}_{uuid.uuid4().hex[:6]}"


def validate_approval_id(approval_id: str) -> None:
    """
    校验 approval_id，防止路径穿越。
    """
    pattern = r"^approval_\d{8}_\d{6}_[a-fA-F0-9]{6}$"

    if not re.match(pattern, approval_id):
        raise ValueError("非法 approval_id 格式")


def save_pending_action(
    *,
    agent_name: str,
    user_message: str,
    tool_name: str,
    tool_args: dict[str, Any],
    reason: str,
    risk_level: str = "high",
) -> dict[str, Any]:
    """
    保存一个等待用户确认的工具动作。
    """
    PENDING_DIR.mkdir(parents=True, exist_ok=True)

    approval_id = create_approval_id()
    pending_file = PENDING_DIR / f"{approval_id}.json"

    data = {
        "approval_id": approval_id,
        "agent_name": agent_name,
        "user_message": user_message,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "risk_level": risk_level,
        "reason": reason,
        "status": "pending",
        "created_at": now_iso(),
        "pending_path": str(pending_file.relative_to(WORKSPACE_ROOT)),
    }

    pending_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    return data


def read_pending_action(approval_id: str) -> dict[str, Any] | None:
    """
    读取一个待确认动作。
    """
    validate_approval_id(approval_id)

    pending_file = (PENDING_DIR / f"{approval_id}.json").resolve()

    try:
        pending_file.relative_to(PENDING_DIR.resolve())
    except ValueError:
        raise ValueError("禁止访问待确认动作目录之外的文件")

    if not pending_file.exists():
        return None

    try:
        return json.loads(pending_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def delete_pending_action(approval_id: str) -> None:
    """
    删除一个待确认动作。
    """
    validate_approval_id(approval_id)

    pending_file = (PENDING_DIR / f"{approval_id}.json").resolve()

    try:
        pending_file.relative_to(PENDING_DIR.resolve())
    except ValueError:
        raise ValueError("禁止访问待确认动作目录之外的文件")

    if pending_file.exists():
        pending_file.unlink()