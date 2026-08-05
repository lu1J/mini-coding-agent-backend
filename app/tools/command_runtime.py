from __future__ import annotations

import subprocess
import sys


PYTHON_ALIASES = {"python", "python3", "py"}
PYTEST_ALIASES = {"pytest", "py.test"}


def resolve_runtime_command_parts(
    command_parts: list[str],
) -> list[str]:
    """
    将白名单命令绑定到当前服务进程的 Python 解释器。

    安全校验仍应在调用本函数之前基于用户原始命令完成。
    """
    if not command_parts:
        return []

    executable = command_parts[0].strip().lower()

    if executable in PYTHON_ALIASES:
        return [sys.executable, *command_parts[1:]]

    if executable in PYTEST_ALIASES:
        return [sys.executable, "-m", "pytest", *command_parts[1:]]

    return list(command_parts)


def format_runtime_command(
    command_parts: list[str],
) -> str:
    """使用平台原生命令行转义规则展示真实执行命令。"""
    return subprocess.list2cmdline(command_parts)
