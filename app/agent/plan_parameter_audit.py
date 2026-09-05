from __future__ import annotations

from typing import Any

from app.tools.file_tools import canonical_workspace_relative


PARAMETER_AUDIT_VERSION = "1.0"

PARAMETER_STATUS_ALIGNED = "aligned"
PARAMETER_STATUS_EXPANDED_READ = "expanded_read_scope"
PARAMETER_STATUS_REQUIRES_REVIEW = "requires_review"
PARAMETER_STATUS_UNSCOPED = "unscoped"
PARAMETER_STATUS_NOT_APPLICABLE = "not_applicable"


# 当前项目中的主要写工具，同时预留后续工具名称。
WRITE_TOOL_NAMES = {
    "edit_file",
    "write_new_file",
    "delete_file",
    "apply_patch",
    "move_file",
    "rename_file",
    "ensure_gitignore",
    "restore_file",
    "apply_workspace_patch",
}

COMMAND_TOOL_NAMES = {
    "run_command",
}

# 这些参数通常表示工具真正操作的目标文件或目录。
PRIMARY_PATH_ARGUMENT_KEYS = {
    "path",
    "file_path",
    "target_path",
    "source_path",
    "destination_path",
    "old_path",
    "new_path",
    "output_path",
}

# 这些参数表示搜索或解析边界，不等同于实际目标文件。
SCOPE_PATH_ARGUMENT_KEYS = {
    "project_root",
    "root",
    "directory",
    "cwd",
}

COMMAND_ARGUMENT_KEYS = (
    "command",
    "cmd",
)


def deduplicate_keep_order(items: list[str]) -> list[str]:
    """去重，同时保留第一次出现时的顺序。"""
    seen: set[str] = set()
    result: list[str] = []

    for item in items:
        normalized = str(item or "").strip()

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        result.append(normalized)

    return result


def normalize_audit_path(value: Any) -> str:
    """
    将 Windows 和 POSIX 路径统一成可比较形式。

    示例：
        .\\demo_project\\models.py
        ./demo_project/models.py

    都会得到：
        demo_project/models.py
    """
    raw_path = str(value or "").strip().strip("`\"'")

    if not raw_path:
        return ""

    path = raw_path.replace("\\", "/")

    while "//" in path:
        path = path.replace("//", "/")

    # 保留 Windows 盘符，但统一盘符大小。
    drive = ""

    if len(path) >= 2 and path[1] == ":":
        drive = path[:2].lower()
        path = path[2:]

    parts: list[str] = []

    for part in path.split("/"):
        if not part or part == ".":
            continue

        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append(part)
            continue

        parts.append(part)

    normalized = "/".join(parts)

    if drive:
        normalized = f"{drive}/{normalized}" if normalized else drive

    return normalized.rstrip("/") or "."


def normalize_command_text(value: Any) -> str:
    """统一命令文本，便于检查命令是否提到目标路径。"""
    return " ".join(
        str(value or "")
        .replace("\\", "/")
        .split()
    ).strip()


def flatten_string_values(value: Any) -> list[str]:
    """把字符串或字符串列表统一转换成字符串列表。"""
    if isinstance(value, str):
        return [value]

    if isinstance(value, (list, tuple, set)):
        return [
            item
            for item in value
            if isinstance(item, str)
        ]

    return []


def extract_planned_target_paths(
    task_plan: dict[str, Any] | None,
) -> list[str]:
    """从 Task Planner 的 target_paths 中提取并标准化计划目标。

    Day20 P0：计划目标统一经过 canonical_workspace_relative 归一到
    workspace 相对形式（Planner 输出的 "workspace/foo.py" 与工具侧
    "foo.py" 将在此收敛），无法确认位于 workspace 内的条目直接丢弃
    （写操作会因空计划范围而 fail closed）。
    """
    if not task_plan:
        return []

    raw_paths = task_plan.get("target_paths", [])

    if not isinstance(raw_paths, list):
        return []

    return deduplicate_keep_order(
        [
            canonical
            for raw_path in raw_paths
            if isinstance(raw_path, str)
            if (canonical := canonical_workspace_relative(raw_path))
        ]
    )


def extract_tool_call_steps(
    execution_steps: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """只保留已经真正执行的工具调用。"""
    if not execution_steps:
        return []

    return [
        step
        for step in execution_steps
        if (
            isinstance(step, dict)
            and step.get("type") == "tool_call"
            and isinstance(step.get("tool_name"), str)
            and str(step.get("tool_name")).strip()
        )
    ]


def path_matches_scope(
    actual_path: str,
    planned_path: str,
) -> bool:
    """
    判断实际路径是否位于计划目标范围内。

    target_paths 既可能是文件，也可能是目录：
    - 完全相同：匹配；
    - 实际路径位于计划目录下：匹配。

    Day20 P0：双方都先经过 canonical_workspace_relative 归一，
    避免 "workspace/foo.py" 与 "foo.py" 这类同义表述被误判为越界；
    任何一方无法确认位于 workspace 内（含 ../ 越界）→ 不匹配。
    """
    actual = canonical_workspace_relative(actual_path)
    planned = canonical_workspace_relative(planned_path)

    if actual is None or planned is None:
        return False

    if actual == planned:
        return True

    return actual.startswith(planned + "/")


def path_matches_any_scope(
    actual_path: str,
    planned_paths: list[str],
) -> bool:
    """判断实际路径是否落在任意计划目标范围内。"""
    return any(
        path_matches_scope(actual_path, planned_path)
        for planned_path in planned_paths
    )


def command_mentions_target(
    command: str,
    target_path: str,
) -> bool:
    """使用标准化字符串判断命令中是否明确提到目标路径。"""
    normalized_command = normalize_command_text(command).lower()
    canonical_target = canonical_workspace_relative(target_path)
    normalized_target = (
        canonical_target.lower() if canonical_target is not None else ""
    )

    return bool(
        normalized_command
        and normalized_target
        and normalized_target in normalized_command
    )


def extract_parameter_events(
    execution_steps: list[dict[str, Any]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """
    从真实工具步骤中提取：
    - 文件或目录路径访问；
    - 作用域参数；
    - 命令执行参数；
    - 无法确认路径的写工具。
    """
    path_accesses: list[dict[str, Any]] = []
    scope_arguments: list[dict[str, Any]] = []
    command_calls: list[dict[str, Any]] = []
    unverifiable_write_tools: list[dict[str, Any]] = []

    for step in extract_tool_call_steps(execution_steps):
        tool_name = str(step["tool_name"]).strip()
        tool_args = step.get("tool_args")

        if not isinstance(tool_args, dict):
            tool_args = {}

        access_mode = (
            "write"
            if tool_name in WRITE_TOOL_NAMES
            else "read"
        )

        tool_primary_paths: list[str] = []

        for argument_name, raw_value in tool_args.items():
            if argument_name in PRIMARY_PATH_ARGUMENT_KEYS:
                for string_value in flatten_string_values(raw_value):
                    normalized_path = normalize_audit_path(string_value)

                    if not normalized_path:
                        continue

                    tool_primary_paths.append(normalized_path)
                    path_accesses.append(
                        {
                            "tool_name": tool_name,
                            "argument": argument_name,
                            "path": normalized_path,
                            "access_mode": access_mode,
                        }
                    )

            elif argument_name in SCOPE_PATH_ARGUMENT_KEYS:
                for string_value in flatten_string_values(raw_value):
                    normalized_path = normalize_audit_path(string_value)

                    if not normalized_path:
                        continue

                    scope_arguments.append(
                        {
                            "tool_name": tool_name,
                            "argument": argument_name,
                            "path": normalized_path,
                        }
                    )

        if tool_name in WRITE_TOOL_NAMES and not tool_primary_paths:
            unverifiable_write_tools.append(
                {
                    "tool_name": tool_name,
                    "reason": "写工具步骤中没有可识别的目标路径参数。",
                }
            )

        if tool_name in COMMAND_TOOL_NAMES:
            command = ""

            for command_key in COMMAND_ARGUMENT_KEYS:
                raw_command = tool_args.get(command_key)

                if isinstance(raw_command, str) and raw_command.strip():
                    command = normalize_command_text(raw_command)
                    break

            cwd = normalize_audit_path(tool_args.get("cwd"))

            command_calls.append(
                {
                    "tool_name": tool_name,
                    "command": command,
                    "cwd": "" if cwd == "." else cwd,
                }
            )

    return {
        "path_accesses": path_accesses,
        "scope_arguments": scope_arguments,
        "command_calls": command_calls,
        "unverifiable_write_tools": unverifiable_write_tools,
    }


def build_parameter_audit(
    *,
    task_plan: dict[str, Any] | None,
    execution_steps: list[dict[str, Any]] | None,
    planned_tools: list[str] | None = None,
) -> dict[str, Any]:
    """构建路径、写范围和命令参数级审计。"""
    planned_tools = planned_tools or []
    planned_target_paths = extract_planned_target_paths(task_plan)
    events = extract_parameter_events(execution_steps)

    path_accesses = events["path_accesses"]
    scope_arguments = events["scope_arguments"]
    command_calls = events["command_calls"]
    unverifiable_write_tools = events["unverifiable_write_tools"]

    actual_unique_paths = deduplicate_keep_order(
        [item["path"] for item in path_accesses]
    )

    matched_target_paths: list[str] = []

    for planned_path in planned_target_paths:
        if any(
            path_matches_scope(item["path"], planned_path)
            for item in path_accesses
        ):
            matched_target_paths.append(planned_path)
            continue

        if any(
            command_mentions_target(
                item.get("command", ""),
                planned_path,
            )
            for item in command_calls
        ):
            matched_target_paths.append(planned_path)

    matched_target_paths = deduplicate_keep_order(matched_target_paths)

    untouched_target_paths = [
        path
        for path in planned_target_paths
        if path not in set(matched_target_paths)
    ]

    read_paths_outside_targets = deduplicate_keep_order(
        [
            item["path"]
            for item in path_accesses
            if (
                item["access_mode"] == "read"
                and planned_target_paths
                and not path_matches_any_scope(
                    item["path"],
                    planned_target_paths,
                )
            )
        ]
    )

    write_paths = deduplicate_keep_order(
        [
            item["path"]
            for item in path_accesses
            if item["access_mode"] == "write"
        ]
    )

    if planned_target_paths:
        write_paths_outside_targets = deduplicate_keep_order(
            [
                path
                for path in write_paths
                if not path_matches_any_scope(
                    path,
                    planned_target_paths,
                )
            ]
        )
    else:
        # 存在写操作但 Planner 没有提取目标路径时，无法确认写范围。
        write_paths_outside_targets = list(write_paths)

    command_execution_planned = "run_command" in set(planned_tools)
    unexpected_command_execution = bool(
        command_calls
        and not command_execution_planned
    )

    command_calls_with_audit: list[dict[str, Any]] = []

    for item in command_calls:
        mentioned_targets = [
            target_path
            for target_path in planned_target_paths
            if command_mentions_target(
                item.get("command", ""),
                target_path,
            )
        ]

        command_calls_with_audit.append(
            {
                **item,
                "planned": command_execution_planned,
                "mentioned_target_paths": mentioned_targets,
            }
        )

    unverified_write_scope = bool(
        unverifiable_write_tools
        or (
            write_paths
            and not planned_target_paths
        )
    )

    requires_review = bool(
        write_paths_outside_targets
        or unexpected_command_execution
        or unverified_write_scope
    )

    expanded_read_scope = bool(
        read_paths_outside_targets
    )

    has_parameter_activity = bool(
        path_accesses
        or scope_arguments
        or command_calls
        or unverifiable_write_tools
    )

    if not has_parameter_activity:
        status = PARAMETER_STATUS_NOT_APPLICABLE
    elif requires_review:
        status = PARAMETER_STATUS_REQUIRES_REVIEW
    elif not planned_target_paths:
        status = PARAMETER_STATUS_UNSCOPED
    elif expanded_read_scope:
        status = PARAMETER_STATUS_EXPANDED_READ
    else:
        status = PARAMETER_STATUS_ALIGNED

    notes: list[str] = []

    if read_paths_outside_targets:
        notes.append(
            "实际读取范围超出了 Planner 提取的目标路径："
            + "、".join(read_paths_outside_targets)
            + "。只读扩展可能是依赖分析需要，但应保留审计记录。"
        )

    if write_paths_outside_targets:
        notes.append(
            "发现超出计划目标范围的写路径："
            + "、".join(write_paths_outside_targets)
            + "。"
        )

    if unexpected_command_execution:
        notes.append(
            "实际执行了 run_command，但 Planner 没有把命令执行列入计划。"
        )

    if unverifiable_write_tools:
        notes.append(
            "存在无法从参数中确认目标路径的写工具："
            + "、".join(
                deduplicate_keep_order(
                    [
                        item["tool_name"]
                        for item in unverifiable_write_tools
                    ]
                )
            )
            + "。"
        )

    if untouched_target_paths:
        notes.append(
            "以下计划目标没有在工具路径参数或命令中明确出现："
            + "、".join(untouched_target_paths)
            + "。"
        )

    return {
        "version": PARAMETER_AUDIT_VERSION,
        "status": status,
        "planned_target_paths": planned_target_paths,
        "actual_path_accesses": path_accesses,
        "scope_arguments": scope_arguments,
        "actual_unique_paths": actual_unique_paths,
        "matched_target_paths": matched_target_paths,
        "untouched_target_paths": untouched_target_paths,
        "read_paths_outside_targets": read_paths_outside_targets,
        "write_paths": write_paths,
        "write_paths_outside_targets": write_paths_outside_targets,
        "write_scope_verified": bool(
            not unverified_write_scope
            and not write_paths_outside_targets
        ),
        "unverifiable_write_tools": unverifiable_write_tools,
        "command_execution_planned": command_execution_planned,
        "command_calls": command_calls_with_audit,
        "unexpected_command_execution": unexpected_command_execution,
        "expanded_read_scope": expanded_read_scope,
        "requires_review": requires_review,
        "notes": notes,
    }
