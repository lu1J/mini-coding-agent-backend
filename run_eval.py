import hashlib
import json
import os
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent

API_URL = "http://127.0.0.1:8000/agent/code"

TASK_FILE = PROJECT_ROOT / "eval_tasks.json"
VERSION_FILE = PROJECT_ROOT / "VERSION"
LATEST_RESULT_FILE = PROJECT_ROOT / "eval_result.json"

load_dotenv(PROJECT_ROOT / ".env")


def read_project_version() -> str:
    """
    读取 VERSION 文件中的项目版本。
    """

    if not VERSION_FILE.exists():
        return "unknown"

    version = VERSION_FILE.read_text(
        encoding="utf-8",
    ).strip()

    return version or "unknown"


def get_git_commit() -> str:
    """
    获取当前 Git 提交哈希。

    获取失败时返回 unknown，
    不让评估脚本因为 Git 信息失败而崩溃。
    """

    try:
        result = subprocess.run(
            [
                "git",
                "rev-parse",
                "HEAD",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            return "unknown"

        return result.stdout.strip() or "unknown"

    except OSError:
        return "unknown"


def is_git_worktree_dirty() -> bool | None:
    """
    判断 Git 工作区是否存在未提交修改。

    返回：
    - True：存在未提交修改；
    - False：工作区干净；
    - None：无法获得 Git 状态。
    """

    try:
        result = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            return None

        return bool(result.stdout.strip())

    except OSError:
        return None


def calculate_file_sha256(path: Path) -> str:
    """
    计算文件的 SHA-256。

    使用二进制读取，避免文本编码和换行转换
    影响读取过程。
    """

    sha256 = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(8192),
            b"",
        ):
            sha256.update(chunk)

    return sha256.hexdigest()


def build_eval_metadata(
    *,
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    """
    构建本次 Agent Eval 的可追溯元数据。

    注意：
    这里只记录模型名称，不记录 API Key。
    """

    return {
        "project_version": read_project_version(),
        "git_commit": get_git_commit(),
        "git_worktree_dirty": is_git_worktree_dirty(),
        "run_started_at": started_at.isoformat(),
        "run_finished_at": finished_at.isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "model_provider": "DeepSeek",
        "model_name": os.getenv(
            "DEEPSEEK_MODEL",
            "unknown",
        ),
        "api_url": API_URL,
        "task_file": TASK_FILE.name,
        "task_file_sha256": calculate_file_sha256(
            TASK_FILE
        ),
    }


def load_tasks() -> list[dict[str, Any]]:
    if not TASK_FILE.exists():
        raise FileNotFoundError(f"找不到评估任务文件：{TASK_FILE}")

    return json.loads(TASK_FILE.read_text(encoding="utf-8"))


def call_agent(message: str, max_steps: int) -> tuple[dict[str, Any] | None, float, str | None]:
    """
    调用本地 /agent/code 接口。

    返回：
    - response_json
    - duration_ms
    - error_message
    """
    started = time.perf_counter()

    try:
        response = requests.post(
            API_URL,
            json={
                "message": message,
                "max_steps": max_steps,
            },
            timeout=120,
        )

        duration_ms = (time.perf_counter() - started) * 1000

        if response.status_code != 200:
            return None, duration_ms, f"HTTP {response.status_code}: {response.text}"

        return response.json(), duration_ms, None

    except Exception as e:
        duration_ms = (time.perf_counter() - started) * 1000
        return None, duration_ms, str(e)


def get_step_tools(result: dict[str, Any]) -> list[str]:
    tools = []

    for step in result.get("steps", []):
        tool_name = step.get("tool_name")
        if tool_name:
            tools.append(tool_name)

    return tools


def get_step_error_types(result: dict[str, Any]) -> list[str]:
    error_types = []

    for step in result.get("steps", []):
        error = step.get("error")
        if isinstance(error, dict) and error.get("type"):
            error_types.append(error["type"])

    return error_types


def has_expected_step_success(result: dict[str, Any], expected_success: bool) -> bool:
    """
    判断是否至少有一个工具步骤的 success 符合预期。
    """
    for step in result.get("steps", []):
        if step.get("type") == "tool_call" and step.get("success") is expected_success:
            return True

    return False


def check_task(task: dict[str, Any], result: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    检查单个任务是否符合预期。
    """
    errors: list[str] = []

    expected_status = task.get("expected_status")
    actual_status = result.get("status")

    if expected_status and actual_status != expected_status:
        errors.append(f"status 不匹配：期望 {expected_status}，实际 {actual_status}")

    tools = get_step_tools(result)

    expected_tools_all = task.get("expected_tools_all") or []
    for tool in expected_tools_all:
        if tool not in tools:
            errors.append(f"缺少必须工具：{tool}，实际工具：{tools}")

    expected_tools_any = task.get("expected_tools_any") or []
    if expected_tools_any:
        if not any(tool in tools for tool in expected_tools_any):
            errors.append(f"没有出现任意期望工具：{expected_tools_any}，实际工具：{tools}")

    expected_pending_tool = task.get("expected_pending_tool")
    if expected_pending_tool:
        pending_action = result.get("pending_action") or {}
        pending_tool = pending_action.get("tool_name")
        if pending_tool != expected_pending_tool:
            errors.append(f"pending tool 不匹配：期望 {expected_pending_tool}，实际 {pending_tool}")

    expected_step_error_type = task.get("expected_step_error_type")
    if expected_step_error_type:
        error_types = get_step_error_types(result)
        if expected_step_error_type not in error_types:
            errors.append(f"缺少期望错误类型：{expected_step_error_type}，实际错误类型：{error_types}")

    if "expected_step_success" in task:
        expected_success = task["expected_step_success"]
        if not has_expected_step_success(result, expected_success):
            errors.append(f"没有找到 success={expected_success} 的工具步骤")

    expected_answer_contains_any = task.get("expected_answer_contains_any") or []
    if expected_answer_contains_any:
        answer = result.get("answer") or ""
        if not any(keyword in answer for keyword in expected_answer_contains_any):
            errors.append(
                f"answer 未包含任意关键词：{expected_answer_contains_any}"
            )

    return len(errors) == 0, errors


def main():
    started_at = datetime.now(timezone.utc)

    tasks = load_tasks()

    print("=" * 80)
    print("Mini Coding Agent Eval")
    print("=" * 80)
    print(f"任务数量：{len(tasks)}")
    print(f"接口地址：{API_URL}")
    print()

    passed = 0
    failed = 0
    total_duration_ms = 0.0
    total_tool_calls = 0

    details = []

    for index, task in enumerate(tasks, start=1):
        task_id = task["id"]
        task_name = task["name"]
        message = task["message"]
        max_steps = task.get("max_steps", 5)

        print(f"[{index}/{len(tasks)}] {task_id} - {task_name}")

        result, duration_ms, call_error = call_agent(
            message=message,
            max_steps=max_steps,
        )

        total_duration_ms += duration_ms

        if call_error:
            failed += 1
            print(f"  ❌ 接口调用失败：{call_error}")
            details.append({
                "id": task_id,
                "name": task_name,
                "passed": False,
                "errors": [call_error],
                "duration_ms": round(duration_ms, 2),
            })
            print()
            continue

        assert result is not None

        tools = get_step_tools(result)
        total_tool_calls += len(tools)

        ok, errors = check_task(task, result)

        if ok:
            passed += 1
            print(f"  ✅ 通过")
        else:
            failed += 1
            print(f"  ❌ 失败")
            for err in errors:
                print(f"     - {err}")

        print(f"  status: {result.get('status')}")
        print(f"  tools: {tools}")
        print(f"  duration: {duration_ms:.0f} ms")
        print(f"  run_id: {result.get('run_id')}")
        print()

        details.append({
            "id": task_id,
            "name": task_name,
            "passed": ok,
            "status": result.get("status"),
            "tools": tools,
            "errors": errors,
            "duration_ms": round(duration_ms, 2),
            "run_id": result.get("run_id"),
        })

    success_rate = passed / len(tasks) * 100 if tasks else 0
    avg_duration = total_duration_ms / len(tasks) if tasks else 0
    avg_tools = total_tool_calls / len(tasks) if tasks else 0

    print("=" * 80)
    print("Eval Summary")
    print("=" * 80)
    print(f"总任务数：{len(tasks)}")
    print(f"通过：{passed}")
    print(f"失败：{failed}")
    print(f"成功率：{success_rate:.1f}%")
    print(f"平均耗时：{avg_duration:.0f} ms")
    print(f"平均工具调用数：{avg_tools:.2f}")

    finished_at = datetime.now(timezone.utc)

    metadata = build_eval_metadata(
        started_at=started_at,
        finished_at=finished_at,
    )

    output = {
        "metadata": metadata,
        "total": len(tasks),
        "passed": passed,
        "failed": failed,
        "success_rate": success_rate,
        "avg_duration_ms": avg_duration,
        "avg_tool_calls": avg_tools,
        "details": details,
    }

    output_path = LATEST_RESULT_FILE
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print("Eval Metadata")
    print("-" * 80)
    print(f"项目版本：{metadata['project_version']}")
    print(f"Git Commit：{metadata['git_commit']}")
    print(
        "Git 工作区存在未提交修改："
        f"{metadata['git_worktree_dirty']}"
    )
    print(f"模型：{metadata['model_name']}")
    print(f"任务集 SHA-256：{metadata['task_file_sha256']}")
    
    print(f"评估结果已保存：{output_path}")


if __name__ == "__main__":
    main()