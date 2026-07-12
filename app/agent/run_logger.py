import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path("workspace").resolve()
RUN_LOG_DIR = WORKSPACE_ROOT / ".agent_runs"


def get_now_text() -> str:
    """
    返回适合写入文件名的当前时间字符串。
    """
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_now_iso() -> str:
    """
    返回适合写入 JSON 的当前时间字符串。
    """
    return datetime.now().isoformat(timespec="seconds")

def save_agent_run(
        *,
        agent_name: str,
        user_message: str,
        status: str,
        answer: str,
        steps: list[dict[str, Any]],
        max_steps: int,
        model_name: str,
        error: dict[str, Any] | None = None,
        pending_action: dict[str, Any] | None = None,
        task_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    保存一次 Agent 运行日志。

    保存位置：
    workspace/.agent_runs/run_xxx.json
    """
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)

    run_id = f"run_{get_now_text()}_{uuid.uuid4().hex[:6]}"
    log_file = RUN_LOG_DIR / f"{run_id}.json"

    log_data = {
        "run_id": run_id,
        "agent_name": agent_name,
        "model_name": model_name,
        "status": status,
        "user_message": user_message,
        "task_plan": task_plan,
        "answer": answer,
        "max_steps": max_steps,
        "steps": steps,
        "created_at": get_now_iso(),
        "error": error,
        "pending_action": pending_action,
    }

    log_file.write_text(
        json.dumps(log_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    return {
        "run_id": run_id,
        "log_path": str(log_file.relative_to(WORKSPACE_ROOT)),
    }


def list_agent_runs(limit: int = 20) -> list[dict[str, Any]]:
    """
    读取最近的 Agent 运行日志列表。

    返回的是摘要，不返回完整 steps。
    """
    if not RUN_LOG_DIR.exists():
        return []

    # 限制最多读取数量，防止一次返回太多
    limit = max(1, min(limit, 100))

    log_files = sorted(
        RUN_LOG_DIR.glob("run_*.json"),
        key=lambda file: file.stat().st_mtime,
        reverse=True
    )

    runs = []

    for log_file in log_files[:limit]:
        try:
            data = json.loads(log_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        answer = data.get("answer", "")
        answer_preview = answer[:120] + "..." if len(answer) > 120 else answer
        error = data.get("error") or {}

        runs.append({
            "run_id": data.get("run_id", log_file.stem),
            "agent_name": data.get("agent_name", ""),
            "model_name": data.get("model_name", ""),
            "status": data.get("status", ""),
            "user_message": data.get("user_message", ""),
            "answer_preview": answer_preview,
            "created_at": data.get("created_at", ""),
            "log_path": str(log_file.relative_to(WORKSPACE_ROOT)),
            "error_type": error.get("type"),
            "error_message": error.get("message"),
        })

    return runs


def validate_run_id(run_id: str) -> None:
    """
    校验 run_id，防止用户通过路径参数访问任意文件。

    合法格式示例：
    run_20260630_143136_da08e7
    """
    pattern = r"^run_\d{8}_\d{6}_[a-fA-F0-9]{6}$"

    if not re.match(pattern, run_id):
        raise ValueError("非法 run_id 格式")


def read_agent_run(run_id: str) -> dict[str, Any] | None:
    """
    根据 run_id 读取某一次 Agent 运行详情。
    """
    validate_run_id(run_id)

    log_file = (RUN_LOG_DIR / f"{run_id}.json").resolve()

    try:
        log_file.relative_to(RUN_LOG_DIR.resolve())
    except ValueError:
        raise ValueError("禁止访问运行日志目录之外的文件")

    if not log_file.exists():
        return None

    try:
        data = json.loads(log_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    data["log_path"] = str(log_file.relative_to(WORKSPACE_ROOT))

    return data