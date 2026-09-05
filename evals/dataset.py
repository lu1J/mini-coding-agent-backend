"""Golden Dataset（固定标准评测集）加载与校验。

职责：
- 读取 evals/tasks/golden_v1.json；
- 计算任务集文件的 SHA-256（版本对比用）；
- 每个任务的结构校验（schema 合法性）；
- fixture 目录定位与隔离副本创建。

本模块不发送 HTTP，不调用模型。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVALS_DIR = Path(__file__).resolve().parent
TASKS_FILE = EVALS_DIR / "tasks" / "golden_v1.json"
FIXTURE_DIR = EVALS_DIR / "fixtures" / "base_project"

# 任务运行时的隔离工作区根目录（相对项目根 / workspace）。
# 注意：不能用 .eval_cases 这类点开头目录——工具层的 should_ignore_path
# 会把点开头目录当作隐藏项忽略，导致 search_code 等遍历不到。
CASE_ROOT_RELATIVE = Path("workspace") / "eval_cases"
EVAL_ARCHIVE_RELATIVE = Path("workspace") / ".agent_runs" / "evals"

ALLOWED_CATEGORIES = {
    "read",
    "search",
    "structure",
    "dependency",
    "modify_approval",
    "create_file_approval",
    "modify_verify",
    "verify_command",
    "failure_recovery",
    "modify_approval_reject",
    "multi_step",
    "max_steps_guard",
    "execution_guardrail",
    "diff_verify",
}

ALLOWED_VERIFY_TYPES = {
    "file_content_contains",
    "file_not_exists",
    "file_exists",
    "file_unchanged",
    "file_sha256",
    "command_output_contains",
    "dependency_edge_present",
    "diff_presence",
    "tool_result_contains",
}

ALLOWED_APPROVAL_FLOWS = {"none", "approve", "reject"}

FIXTURE_EXPECTED_FILES = {
    "README.md",
    "main.py",
    "api.py",
    "service.py",
    "models.py",
    "math_utils.py",
    "utils.py",
    "conftest.py",
    "tests/test_math_utils.py",
    "tests/test_utils.py",
}


def load_dataset() -> dict[str, Any]:
    """读取 golden_v1.json。"""
    if not TASKS_FILE.exists():
        raise FileNotFoundError(f"找不到评测任务集：{TASKS_FILE}")
    try:
        return __import__("json").loads(TASKS_FILE.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"评测任务集 JSON 解析失败：{error}") from error


def load_golden_tasks() -> list[dict[str, Any]]:
    data = load_dataset()
    tasks = data.get("tasks") or []
    if not isinstance(tasks, list):
        raise ValueError("任务集格式错误：tasks 必须是列表")
    return [task for task in tasks if isinstance(task, dict)]


def dataset_sha256() -> str:
    """任务集文件字节的 SHA-256（版本指纹）。"""
    digest = hashlib.sha256()
    with TASKS_FILE.open("rb") as file:
        for chunk in iter(lambda: file.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(problems: list[str], message: str) -> None:
    problems.append(message)


def validate_task(task: dict[str, Any], index: int) -> list[str]:
    """校验单个任务结构，返回问题列表（空 = 合法）。"""
    problems: list[str] = []
    prefix = f"任务[{index}]"
    task_id = task.get("id")

    required_top = {
        "id": str,
        "name": str,
        "category": str,
        "max_steps": int,
        "prompt_template": str,
        "expected": dict,
        "approval": dict,
        "verify": list,
    }
    for field, field_type in required_top.items():
        if not isinstance(task.get(field), field_type):
            _fail(problems, f"{prefix} 缺少或类型错误的字段：{field}（期望 {field_type.__name__}）")

    if task_id is None:
        return problems
    tag = f"{prefix}({task_id})"

    if task.get("category") not in ALLOWED_CATEGORIES:
        _fail(problems, f"{tag} category 不在允许集合：{task.get('category')}")
    if not str(task.get("prompt_template") or "").strip():
        _fail(problems, f"{tag} prompt_template 为空")
    if "{case_root}" not in str(task.get("prompt_template") or ""):
        _fail(problems, f"{tag} prompt_template 未包含 {{case_root}} 占位符")
    if isinstance(task.get("max_steps"), int):
        # 与后端契约耦合：AgentRequest.max_steps 上限为 10（app/schemas.py）。
        if not 1 <= task["max_steps"] <= 10:
            _fail(problems, f"{tag} max_steps 必须在 1..10（后端 API 上限），实际 {task['max_steps']}")

    expected = task.get("expected")
    if isinstance(expected, dict):
        allowed_statuses = expected.get("allowed_statuses")
        if not isinstance(allowed_statuses, list) or not allowed_statuses:
            _fail(problems, f"{tag} expected.allowed_statuses 必须是非空列表")
        for tool_name in expected.get("required_tools") or []:
            if not isinstance(tool_name, str) or not tool_name.strip():
                _fail(problems, f"{tag} required_tools 元素必须是字符串")
        if not isinstance(expected.get("plan_completion_min", 0.0), (int, float)):
            _fail(problems, f"{tag} plan_completion_min 必须是数字")

    approval = task.get("approval")
    if isinstance(approval, dict):
        flow = approval.get("flow", "none")
        if flow not in ALLOWED_APPROVAL_FLOWS:
            _fail(problems, f"{tag} approval.flow 不在允许集合：{flow}")
        if flow != "none" and not approval.get("expected_pending_tool"):
            _fail(problems, f"{tag} approval 流程需要 expected_pending_tool")
    else:
        _fail(problems, f"{tag} approval 必须是对象")

    verify = task.get("verify")
    if isinstance(verify, list):
        for item in verify:
            if not isinstance(item, dict):
                _fail(problems, f"{tag} verify 元素必须是对象")
                continue
            vtype = item.get("type")
            if vtype not in ALLOWED_VERIFY_TYPES:
                _fail(problems, f"{tag} verify.type 不在允许集合：{vtype}")
            if not item.get("path") and vtype not in {
                "command_output_contains",
                "dependency_edge_present",
                # 以下两类校验的是工具输出（path_hint）而非文件：
                "tool_result_contains",
            }:
                _fail(problems, f"{tag} verify({vtype}) 缺少 path")
    else:
        _fail(problems, f"{tag} verify 必须是列表")

    if approval is not None and isinstance(approval, dict):
        if approval.get("flow") != "none" and not isinstance(task.get("max_steps"), int):
            _fail(problems, f"{tag} max_steps 必须是整数")

    return problems


def validate_dataset(tasks: list[dict[str, Any]]) -> list[str]:
    """校验整个任务集（唯一 id + 每任务结构）。"""
    problems: list[str] = []
    seen_ids: set[str] = set()
    for index, task in enumerate(tasks):
        problems.extend(validate_task(task, index))
        task_id = task.get("id")
        if task_id in seen_ids:
            problems.append(f"任务 id 重复：{task_id}")
        if task_id:
            seen_ids.add(task_id)
    smoke_ids = [task["id"] for task in tasks if task.get("smoke")]
    if not smoke_ids:
        problems.append("任务集没有任何 smoke 任务")
    if len(tasks) < 3:
        problems.append("任务集任务数量过少（少于 3）")
    return problems


def case_dirs(eval_run_id: str, task_id: str) -> tuple[Path, Path]:
    """返回 (隔离目录绝对路径, workspace 相对路径)。

    约定目录结构：
        workspace/eval_cases/<eval_run_id>/<task_id>/demo_project/
    """
    absolute = PROJECT_ROOT / CASE_ROOT_RELATIVE / eval_run_id / task_id / "demo_project"
    relative = (CASE_ROOT_RELATIVE / eval_run_id / task_id / "demo_project").as_posix()
    return absolute, relative


def copy_fixture_into_case(demo_project_dir: Path) -> None:
    """把 base fixture 完整复制到隔离 case（每次任务全新副本）。"""
    if demo_project_dir.exists():
        shutil.rmtree(demo_project_dir)
    shutil.copytree(FIXTURE_DIR, demo_project_dir)
    # 清理可能的 Python 缓存残留。
    for cache in demo_project_dir.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)


def fixture_files_exist() -> list[str]:
    """返回 fixture 中缺失的文件列表（空 = 完整）。"""
    missing: list[str] = []
    for relative in FIXTURE_EXPECTED_FILES:
        if not (FIXTURE_DIR / relative).exists():
            missing.append(relative)
    return missing
