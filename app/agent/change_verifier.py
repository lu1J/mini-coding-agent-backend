from __future__ import annotations

import difflib
import hashlib
import json
import py_compile
import subprocess
import sys
import time
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.agent.execution_policy_guard import extract_primary_paths


VERIFIER_VERSION = "1.0"
WORKSPACE_ROOT = Path("workspace").resolve()

VERIFY_STATUS_PASSED = "passed"
VERIFY_STATUS_PASSED_WITH_WARNINGS = "passed_with_warnings"
VERIFY_STATUS_FAILED_ROLLED_BACK = "failed_rolled_back"
VERIFY_STATUS_FAILED_ROLLBACK_ERROR = "failed_rollback_error"
VERIFY_STATUS_FAILED = "failed"

BLOCKING_CHECK_STATUSES = {"failed", "error"}
MAX_DIFF_CHARS = 12_000
TEST_TIMEOUT_SECONDS = 60


@dataclass
class FileSnapshot:
    """一次修改前的单路径快照，仅在当前请求内存中使用。"""

    relative_path: str
    absolute_path: Path
    existed: bool
    was_file: bool
    content: bytes | None
    sha256: str | None
    role: str = "target"


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_workspace_path(relative_path: str) -> Path:
    """把相对路径安全解析到 workspace 内，禁止路径穿越。"""
    raw_path = str(relative_path or "").strip().replace("\\", "/")

    if not raw_path:
        raise ValueError("目标路径为空。")

    candidate = (WORKSPACE_ROOT / raw_path).resolve()

    try:
        candidate.relative_to(WORKSPACE_ROOT)
    except ValueError as error_value:
        raise ValueError(f"目标路径超出 workspace：{relative_path}") from error_value

    return candidate


def _display_relative_path(path: Path) -> str:
    return path.relative_to(WORKSPACE_ROOT).as_posix()


def create_change_snapshot(
    tool_name: str,
    tool_args: dict[str, Any] | None,
) -> list[FileSnapshot]:
    """为写工具目标及可预期副作用创建修改前快照。"""
    requested_paths = extract_primary_paths(tool_args)
    path_specs: list[tuple[Path, str]] = []

    for requested_path in requested_paths:
        absolute_path = _safe_workspace_path(requested_path)
        path_specs.append((absolute_path, "target"))

        # 当前 edit_file 会创建 <文件名>.bak。把备份也纳入事务，
        # 验证失败时才能恢复到真正的修改前状态。
        if tool_name == "edit_file" and absolute_path.suffix:
            backup_path = absolute_path.with_suffix(
                absolute_path.suffix + ".bak"
            )
            path_specs.append((backup_path, "auxiliary_backup"))

    snapshots: list[FileSnapshot] = []
    seen_paths: set[Path] = set()

    for absolute_path, role in path_specs:
        if absolute_path in seen_paths:
            continue
        seen_paths.add(absolute_path)

        existed = absolute_path.exists()
        was_file = absolute_path.is_file() if existed else False
        content: bytes | None = None
        digest: str | None = None

        if existed and was_file:
            content = absolute_path.read_bytes()
            digest = _sha256_bytes(content)

        snapshots.append(
            FileSnapshot(
                relative_path=_display_relative_path(absolute_path),
                absolute_path=absolute_path,
                existed=existed,
                was_file=was_file,
                content=content,
                sha256=digest,
                role=role,
            )
        )

    return snapshots


def _current_state(snapshot: FileSnapshot) -> dict[str, Any]:
    path = snapshot.absolute_path
    exists = path.exists()
    is_file = path.is_file() if exists else False
    content: bytes | None = None
    digest: str | None = None

    if exists and is_file:
        content = path.read_bytes()
        digest = _sha256_bytes(content)

    return {
        "exists": exists,
        "is_file": is_file,
        "content": content,
        "sha256": digest,
    }


def detect_changed_paths(
    snapshots: list[FileSnapshot],
    *,
    include_auxiliary: bool = False,
) -> list[str]:
    """比较快照与当前状态，返回真实发生变化的路径。"""
    changed_paths: list[str] = []

    for snapshot in snapshots:
        if snapshot.role != "target" and not include_auxiliary:
            continue

        current = _current_state(snapshot)

        changed = (
            snapshot.existed != current["exists"]
            or snapshot.was_file != current["is_file"]
            or snapshot.sha256 != current["sha256"]
        )

        if changed:
            changed_paths.append(snapshot.relative_path)

    return changed_paths


def _decode_text(content: bytes | None) -> str | None:
    if content is None:
        return None

    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def build_diff_report(
    snapshots: list[FileSnapshot],
) -> dict[str, Any]:
    """根据修改前快照和当前文件生成结构化 Diff 摘要。"""
    files: list[dict[str, Any]] = []
    total_added = 0
    total_removed = 0

    for snapshot in snapshots:
        if snapshot.role != "target":
            continue

        current = _current_state(snapshot)
        before_text = _decode_text(snapshot.content)
        after_text = _decode_text(current["content"])

        if (
            snapshot.existed == current["exists"]
            and snapshot.sha256 == current["sha256"]
        ):
            continue

        if before_text is None and snapshot.existed:
            files.append(
                {
                    "path": snapshot.relative_path,
                    "status": "binary_or_non_utf8",
                    "added_lines": 0,
                    "removed_lines": 0,
                    "diff": "文件不是 UTF-8 文本，未生成文本 Diff。",
                }
            )
            continue

        if after_text is None and current["exists"]:
            files.append(
                {
                    "path": snapshot.relative_path,
                    "status": "binary_or_non_utf8",
                    "added_lines": 0,
                    "removed_lines": 0,
                    "diff": "修改后的文件不是 UTF-8 文本，未生成文本 Diff。",
                }
            )
            continue

        before_lines = (before_text or "").splitlines()
        after_lines = (after_text or "").splitlines()
        diff_lines = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=(
                    f"a/{snapshot.relative_path}"
                    if snapshot.existed
                    else "/dev/null"
                ),
                tofile=(
                    f"b/{snapshot.relative_path}"
                    if current["exists"]
                    else "/dev/null"
                ),
                lineterm="",
            )
        )

        added_lines = sum(
            1
            for line in diff_lines
            if line.startswith("+") and not line.startswith("+++")
        )
        removed_lines = sum(
            1
            for line in diff_lines
            if line.startswith("-") and not line.startswith("---")
        )
        total_added += added_lines
        total_removed += removed_lines

        diff_text = "\n".join(diff_lines)
        truncated = len(diff_text) > MAX_DIFF_CHARS

        if truncated:
            diff_text = (
                diff_text[:MAX_DIFF_CHARS]
                + "\n[Diff 过长，已截断]"
            )

        if not snapshot.existed and current["exists"]:
            file_status = "created"
        elif snapshot.existed and not current["exists"]:
            file_status = "deleted"
        else:
            file_status = "modified"

        files.append(
            {
                "path": snapshot.relative_path,
                "status": file_status,
                "added_lines": added_lines,
                "removed_lines": removed_lines,
                "truncated": truncated,
                "diff": diff_text,
            }
        )

    return {
        "changed_file_count": len(files),
        "total_added_lines": total_added,
        "total_removed_lines": total_removed,
        "files": files,
    }


def normalize_tool_execution_result(raw_result: Any) -> dict[str, Any]:
    """兼容结构化工具结果和旧版字符串工具结果。"""
    if isinstance(raw_result, dict):
        error = raw_result.get("error")
        success_value = raw_result.get("success")
        success = (
            bool(success_value)
            if success_value is not None
            else not bool(error)
        )

        result_value: Any = None
        for key in ("result", "tool_result", "message", "content"):
            if raw_result.get(key) is not None:
                result_value = raw_result.get(key)
                break

        if result_value is None:
            result_value = json.dumps(
                raw_result,
                ensure_ascii=False,
                default=str,
            )

        return {
            "success": success,
            "result": str(result_value),
            "error": error,
            "raw": raw_result,
        }

    result_text = str(raw_result)
    lower_text = result_text.lower()
    failure_markers = (
        "文件不存在",
        "路径不存在",
        "没有找到要替换",
        "拒绝覆盖",
        "禁止修改",
        "禁止创建",
        "不支持修改",
        "不支持创建",
        "创建文件失败",
        "工具执行失败",
        "error:",
        "traceback",
        "permission denied",
        "not found",
    )
    success = not any(
        marker.lower() in lower_text
        for marker in failure_markers
    )

    return {
        "success": success,
        "result": result_text,
        "error": None if success else {
            "type": "tool_error",
            "message": "写工具返回失败信息。",
            "detail": result_text,
        },
        "raw": raw_result,
    }


def _build_check(
    *,
    name: str,
    status: str,
    message: str,
    blocking: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "blocking": blocking,
        "message": message,
        "details": details or {},
    }


def validate_changed_file_formats(
    changed_paths: list[str],
) -> list[dict[str, Any]]:
    """执行 Python、JSON、TOML 的确定性格式验证。"""
    checks: list[dict[str, Any]] = []

    for relative_path in changed_paths:
        path = _safe_workspace_path(relative_path)

        if not path.exists():
            checks.append(
                _build_check(
                    name="file_removed",
                    status="passed",
                    blocking=False,
                    message=f"文件已按操作删除：{relative_path}",
                )
            )
            continue

        suffix = path.suffix.lower()

        if suffix == ".py":
            try:
                # 把临时 pyc 写入系统临时目录，避免污染 workspace。
                with tempfile.TemporaryDirectory() as temp_dir:
                    compiled_path = (
                        Path(temp_dir)
                        / f"{path.stem}.pyc"
                    )
                    py_compile.compile(
                        str(path),
                        cfile=str(compiled_path),
                        doraise=True,
                    )
                checks.append(
                    _build_check(
                        name="python_syntax",
                        status="passed",
                        blocking=True,
                        message=f"Python 语法检查通过：{relative_path}",
                    )
                )
            except py_compile.PyCompileError as error_value:
                checks.append(
                    _build_check(
                        name="python_syntax",
                        status="failed",
                        blocking=True,
                        message=f"Python 语法检查失败：{relative_path}",
                        details={"error": str(error_value)},
                    )
                )

        elif suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
                checks.append(
                    _build_check(
                        name="json_parse",
                        status="passed",
                        blocking=True,
                        message=f"JSON 解析通过：{relative_path}",
                    )
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as error_value:
                checks.append(
                    _build_check(
                        name="json_parse",
                        status="failed",
                        blocking=True,
                        message=f"JSON 解析失败：{relative_path}",
                        details={"error": str(error_value)},
                    )
                )

        elif suffix == ".toml":
            try:
                tomllib.loads(path.read_text(encoding="utf-8"))
                checks.append(
                    _build_check(
                        name="toml_parse",
                        status="passed",
                        blocking=True,
                        message=f"TOML 解析通过：{relative_path}",
                    )
                )
            except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error_value:
                checks.append(
                    _build_check(
                        name="toml_parse",
                        status="failed",
                        blocking=True,
                        message=f"TOML 解析失败：{relative_path}",
                        details={"error": str(error_value)},
                    )
                )

    if not checks:
        checks.append(
            _build_check(
                name="format_validation",
                status="skipped",
                blocking=False,
                message="本次修改没有可自动解析的 Python、JSON 或 TOML 文件。",
            )
        )

    return checks


def select_targeted_test_files(
    changed_paths: list[str],
) -> list[str]:
    """根据变更文件查找同项目中的定向 pytest 文件。"""
    selected: list[str] = []

    for relative_path in changed_paths:
        changed_path = _safe_workspace_path(relative_path)

        if changed_path.suffix.lower() != ".py":
            continue

        if not changed_path.exists():
            continue

        relative = changed_path.relative_to(WORKSPACE_ROOT)
        parts = relative.parts
        project_root = WORKSPACE_ROOT / parts[0] if len(parts) > 1 else WORKSPACE_ROOT

        if changed_path.name.startswith("test_"):
            selected.append(_display_relative_path(changed_path))
            continue

        test_name = f"test_{changed_path.stem}.py"
        candidates = [
            project_root / "tests" / test_name,
            changed_path.parent / "tests" / test_name,
            changed_path.parent / test_name,
        ]

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                selected.append(_display_relative_path(candidate))

    return list(dict.fromkeys(selected))


def run_targeted_tests(
    changed_paths: list[str],
    *,
    timeout_seconds: int = TEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """仅运行与变更文件直接对应的 pytest，避免无边界全量测试。"""
    test_files = select_targeted_test_files(changed_paths)

    if not test_files:
        return _build_check(
            name="targeted_tests",
            status="skipped",
            blocking=False,
            message="没有找到与变更文件直接对应的 pytest 文件。",
        )

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *test_files,
    ]

    try:
        completed = subprocess.run(
            command,
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as error_value:
        return _build_check(
            name="targeted_tests",
            status="failed",
            blocking=True,
            message="定向测试执行超时。",
            details={
                "test_files": test_files,
                "timeout_seconds": timeout_seconds,
                "error": str(error_value),
            },
        )

    status = "passed" if completed.returncode == 0 else "failed"

    return _build_check(
        name="targeted_tests",
        status=status,
        blocking=True,
        message=(
            "定向测试通过。"
            if completed.returncode == 0
            else "定向测试失败。"
        ),
        details={
            "test_files": test_files,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-6000:],
            "stderr": completed.stderr[-6000:],
        },
    )


def rollback_change(
    snapshots: list[FileSnapshot],
) -> dict[str, Any]:
    """恢复修改前状态，并验证恢复结果。"""
    restored_paths: list[str] = []
    removed_new_paths: list[str] = []
    errors: list[dict[str, str]] = []

    # 先移除修改前不存在、修改后新建的路径。
    for snapshot in reversed(snapshots):
        try:
            if not snapshot.existed and snapshot.absolute_path.exists():
                if snapshot.absolute_path.is_file():
                    snapshot.absolute_path.unlink()
                    removed_new_paths.append(snapshot.relative_path)
                elif snapshot.absolute_path.is_dir():
                    snapshot.absolute_path.rmdir()
                    removed_new_paths.append(snapshot.relative_path)
        except Exception as error_value:
            errors.append(
                {
                    "path": snapshot.relative_path,
                    "error": str(error_value),
                }
            )

    # 再恢复修改前存在的文件内容。
    for snapshot in snapshots:
        if not snapshot.existed:
            continue

        try:
            if snapshot.was_file:
                snapshot.absolute_path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                snapshot.absolute_path.write_bytes(snapshot.content or b"")
                restored_paths.append(snapshot.relative_path)
        except Exception as error_value:
            errors.append(
                {
                    "path": snapshot.relative_path,
                    "error": str(error_value),
                }
            )

    remaining_changes = detect_changed_paths(
        snapshots,
        include_auxiliary=True,
    )
    success = not errors and not remaining_changes

    return {
        "attempted": True,
        "success": success,
        "restored_paths": restored_paths,
        "removed_new_paths": removed_new_paths,
        "remaining_changed_paths": remaining_changes,
        "errors": errors,
    }


def _has_blocking_failure(checks: list[dict[str, Any]]) -> bool:
    return any(
        bool(check.get("blocking"))
        and str(check.get("status")) in BLOCKING_CHECK_STATUSES
        for check in checks
    )


def execute_verified_change(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_func: Callable[..., Any],
    run_tests: bool = False,
) -> dict[str, Any]:
    """
    以事务式流程执行写工具：
    Snapshot → Execute → Diff → Validate → Test → Rollback。
    """
    started_at = time.perf_counter()
    snapshots = create_change_snapshot(tool_name, tool_args)
    checks: list[dict[str, Any]] = []

    if not snapshots:
        return {
            "version": VERIFIER_VERSION,
            "status": VERIFY_STATUS_FAILED,
            "success": False,
            "tool_name": tool_name,
            "requested_paths": [],
            "changed_paths": [],
            "checks": [
                _build_check(
                    name="snapshot",
                    status="failed",
                    blocking=True,
                    message="写工具没有可识别的目标路径，无法创建快照。",
                )
            ],
            "diff": {
                "changed_file_count": 0,
                "total_added_lines": 0,
                "total_removed_lines": 0,
                "files": [],
            },
            "rollback": {
                "attempted": False,
                "success": False,
                "restored_paths": [],
                "removed_new_paths": [],
                "remaining_changed_paths": [],
                "errors": [],
            },
            "tool_execution": None,
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
        }

    try:
        raw_tool_result = tool_func(**tool_args)
        tool_execution = normalize_tool_execution_result(raw_tool_result)
    except Exception as error_value:
        tool_execution = {
            "success": False,
            "result": f"工具执行异常：{str(error_value)}",
            "error": {
                "type": "tool_exception",
                "message": "写工具抛出异常。",
                "detail": str(error_value),
            },
            "raw": None,
        }

    checks.append(
        _build_check(
            name="tool_execution",
            status=("passed" if tool_execution["success"] else "failed"),
            blocking=True,
            message=(
                "写工具执行完成。"
                if tool_execution["success"]
                else "写工具执行失败。"
            ),
            details={"error": tool_execution.get("error")},
        )
    )

    changed_paths = detect_changed_paths(snapshots)
    checks.append(
        _build_check(
            name="change_detected",
            status=("passed" if changed_paths else "warning"),
            blocking=False,
            message=(
                "检测到真实文件变更。"
                if changed_paths
                else "工具未产生可观察的文件变更，可能是幂等操作或无效修改。"
            ),
            details={"changed_paths": changed_paths},
        )
    )

    diff_report = build_diff_report(snapshots)

    if tool_execution["success"]:
        checks.extend(validate_changed_file_formats(changed_paths))

        if run_tests:
            checks.append(run_targeted_tests(changed_paths))
        else:
            checks.append(
                _build_check(
                    name="targeted_tests",
                    status="skipped",
                    blocking=False,
                    message="Planner 未要求测试，本次只执行确定性格式验证。",
                )
            )

    blocking_failure = _has_blocking_failure(checks)
    rollback_report = {
        "attempted": False,
        "success": True,
        "restored_paths": [],
        "removed_new_paths": [],
        "remaining_changed_paths": [],
        "errors": [],
    }

    if blocking_failure:
        rollback_report = rollback_change(snapshots)
        status = (
            VERIFY_STATUS_FAILED_ROLLED_BACK
            if rollback_report["success"]
            else VERIFY_STATUS_FAILED_ROLLBACK_ERROR
        )
        success = False
    else:
        warning_present = any(
            check.get("status") in {"warning", "skipped"}
            for check in checks
        )
        status = (
            VERIFY_STATUS_PASSED_WITH_WARNINGS
            if warning_present
            else VERIFY_STATUS_PASSED
        )
        success = True

    return {
        "version": VERIFIER_VERSION,
        "status": status,
        "success": success,
        "tool_name": tool_name,
        "requested_paths": [
            snapshot.relative_path
            for snapshot in snapshots
            if snapshot.role == "target"
        ],
        "changed_paths": changed_paths,
        "checks": checks,
        "diff": diff_report,
        "rollback": rollback_report,
        "tool_execution": {
            "success": tool_execution["success"],
            "result": tool_execution["result"],
            "error": tool_execution.get("error"),
        },
        "duration_ms": int((time.perf_counter() - started_at) * 1000),
    }
