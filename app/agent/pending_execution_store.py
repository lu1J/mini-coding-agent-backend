from __future__ import annotations

import json
from pathlib import Path
from typing import Any


STORE_VERSION = "1.1"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STORE_DIR = _PROJECT_ROOT / "workspace" / ".agent_executor_pending"


def _context_path(
    approval_id: str,
    *,
    store_dir: Path | None = None,
) -> Path:
    safe_id = "".join(
        ch for ch in approval_id
        if ch.isalnum() or ch in {"_", "-"}
    )
    if not safe_id:
        raise ValueError("approval_id 不能为空。")

    root = store_dir or _DEFAULT_STORE_DIR
    return root / f"{safe_id}.json"


def save_pending_execution_context(
    *,
    approval_id: str,
    task_plan: dict[str, Any],
    executor_state: dict[str, Any],
    max_steps: int,
    steps: list[dict[str, Any]] | None = None,
    store_dir: Path | None = None,
) -> str:
    path = _context_path(approval_id, store_dir=store_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": STORE_VERSION,
        "approval_id": approval_id,
        "task_plan": task_plan,
        "executor_state": executor_state,
        "max_steps": max_steps,
        "steps": list(steps or []),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def read_pending_execution_context(
    approval_id: str,
    *,
    store_dir: Path | None = None,
) -> dict[str, Any] | None:
    path = _context_path(approval_id, store_dir=store_dir)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return payload if isinstance(payload, dict) else None


def delete_pending_execution_context(
    approval_id: str,
    *,
    store_dir: Path | None = None,
) -> None:
    path = _context_path(approval_id, store_dir=store_dir)
    if path.exists():
        path.unlink()
