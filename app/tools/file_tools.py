import difflib
import subprocess
from pathlib import Path
from typing import Any


# Agent 只能访问这个 workspace 目录
WORKSPACE_ROOT = Path("workspace").resolve()


def ensure_workspace_exists() -> None:
    """
    确保 workspace 目录存在。
    """
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)


def make_tool_success(result: str) -> dict[str, Any]:
    """
    构造工具成功结果。
    """
    return {
        "success": True,
        "result": result,
        "error": None,
    }


def make_tool_error(
    error_type: str,
    message: str,
    detail: str | None = None,
    result: str | None = None,
) -> dict[str, Any]:
    """
    构造工具失败结果。
    """
    return {
        "success": False,
        "result": result or message,
        "error": {
            "type": error_type,
            "message": message,
            "detail": detail,
        },
    }


def safe_resolve_path(path: str) -> Path:
    """
    把用户/模型传入的路径转换成安全的绝对路径。

    重点：
    只能访问 workspace 目录里面的文件。
    不允许通过 ../ 跳出 workspace。
    """
    ensure_workspace_exists()

    target_path = (WORKSPACE_ROOT / path).resolve()

    try:
        target_path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise ValueError("禁止访问 workspace 目录之外的路径")

    return target_path


def should_ignore_path(path: Path) -> bool:
    """
    判断某个文件或目录是否应该被 Agent 忽略。

    这些文件通常不应该参与 list_files / search_code：
    - 虚拟环境
    - Git 元数据
    - Python 缓存
    - 备份文件
    - 临时文件
    """
    if path.name == ".gitignore":
        return False
    ignore_names = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".idea",
        "node_modules",
    }

    if path.name in ignore_names:
        return True

    if path.name.startswith("."):
        return True

    if path.suffix in {".bak", ".pyc", ".pyo", ".log", ".tmp"}:
        return True

    return False


def list_files(path: str = ".") -> str:
    """
    列出 workspace 中指定目录下的文件和文件夹。
    """
    target_path = safe_resolve_path(path)

    if not target_path.exists():
        return f"路径不存在：{path}"

    if not target_path.is_dir():
        return f"这不是一个目录：{path}"

    results = []

    for item in sorted(target_path.iterdir()):
        if should_ignore_path(item):
            continue

        item_type = "目录" if item.is_dir() else "文件"
        relative_path = item.relative_to(WORKSPACE_ROOT)
        results.append(f"[{item_type}] {relative_path}")

    if not results:
        return f"目录为空：{path}"

    return "\n".join(results)


def read_file(path: str) -> str:
    """
    读取 workspace 中的文本文件内容。

    注意：
    - 只能读取 workspace 内的文件
    - 禁止读取 .env
    - 禁止读取隐藏文件，但允许 .gitignore
    - 限制文件大小和返回长度
    """
    target_path = safe_resolve_path(path)

    if not target_path.exists():
        return f"文件不存在：{path}"

    if not target_path.is_file():
        return f"目标不是文件：{path}"

    # 禁止读取敏感文件
    if target_path.name == ".env" or ".env" in target_path.parts:
        return "禁止读取 .env 文件。"

    # 禁止读取隐藏文件，但允许读取 .gitignore
    if target_path.name.startswith(".") and target_path.name != ".gitignore":
        return f"禁止读取隐藏文件：{path}"

    # 限制文本文件类型，避免读取二进制文件
    allowed_suffixes = {
        ".py",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".html",
        ".css",
        ".js",
    }

    if target_path.suffix not in allowed_suffixes and target_path.name != ".gitignore":
        return f"不支持读取该类型文件：{target_path.suffix}"

    max_file_size = 100_000

    if target_path.stat().st_size > max_file_size:
        return f"文件过大，拒绝读取：{path}"

    try:
        content = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"文件不是 UTF-8 文本，无法读取：{path}"

    max_chars = 4000

    if len(content) > max_chars:
        return (
            content[:max_chars]
            + "\n\n[内容过长，已截断，只显示前 4000 个字符]"
        )

    return content


def read_file_lines(path: str, start_line: int = 1, end_line: int = 80) -> str:
    """
    读取 workspace 中指定文件的指定行范围。

    参数：
    - path：相对于 workspace 的文件路径
    - start_line：起始行号，从 1 开始
    - end_line：结束行号，包含该行
    """
    target_path = safe_resolve_path(path)

    if not target_path.exists():
        return f"文件不存在：{path}"

    if not target_path.is_file():
        return f"目标不是文件：{path}"

    # 禁止读取敏感文件
    if target_path.name == ".env" or ".env" in target_path.parts:
        return "禁止读取 .env 文件。"

    # 隐藏文件默认不允许读取，但允许读取 .gitignore
    if target_path.name.startswith(".") and target_path.name != ".gitignore":
        return f"禁止读取隐藏文件：{path}"

    # 限制文本文件类型，避免读取二进制文件
    allowed_suffixes = {
        ".py",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".html",
        ".css",
        ".js",
        ".gitignore",
    }

    if target_path.suffix not in allowed_suffixes and target_path.name != ".gitignore":
        return f"不支持读取该类型文件：{target_path.suffix}"

    max_file_size = 200_000

    if target_path.stat().st_size > max_file_size:
        return f"文件过大，拒绝读取：{path}"

    if start_line < 1:
        return "start_line 必须大于等于 1。"

    if end_line < start_line:
        return "end_line 必须大于等于 start_line。"

    max_lines_per_read = 200

    if end_line - start_line + 1 > max_lines_per_read:
        end_line = start_line + max_lines_per_read - 1

    try:
        content = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"文件不是 UTF-8 文本，无法读取：{path}"

    lines = content.splitlines()
    total_lines = len(lines)

    if total_lines == 0:
        return f"文件为空：{path}"

    if start_line > total_lines:
        return (
            f"起始行超过文件总行数。\n"
            f"文件：{path}\n"
            f"总行数：{total_lines}\n"
            f"请求起始行：{start_line}"
        )

    actual_end_line = min(end_line, total_lines)

    output_lines = [
        f"文件：{path}",
        f"总行数：{total_lines}",
        f"读取范围：第 {start_line} 行到第 {actual_end_line} 行",
        "",
    ]

    for line_number in range(start_line, actual_end_line + 1):
        line_content = lines[line_number - 1]
        output_lines.append(f"{line_number}: {line_content}")

    if actual_end_line < end_line:
        output_lines.append("")
        output_lines.append("[已到达文件末尾]")

    return "\n".join(output_lines)


def search_code(keyword: str, path: str = ".") -> str:
    """
    在 workspace 指定目录下递归搜索关键词。

    返回：
    - 文件路径
    - 行号
    - 匹配到的代码行

    注意：
    只能搜索 workspace 目录内的内容。
    """
    target_path = safe_resolve_path(path)

    if not target_path.exists():
        return f"路径不存在：{path}"

    if target_path.is_file():
        files = [target_path]
    else:
        files = list(target_path.rglob("*"))

    allowed_suffixes = {
        ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".html", ".css", ".js"
    }

    results = []
    max_results = 30

    for file_path in files:
        if len(results) >= max_results:
            break

        if should_ignore_path(file_path):
            continue

        # 如果父目录里有应该忽略的目录，也跳过
        if any(should_ignore_path(parent) for parent in file_path.parents):
            continue

        if not file_path.is_file():
            continue

        if file_path.suffix not in allowed_suffixes:
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for line_no, line in enumerate(content.splitlines(), start=1):
            if keyword.lower() in line.lower():
                relative_path = file_path.relative_to(WORKSPACE_ROOT)
                results.append(
                    f"{relative_path}:{line_no}: {line.strip()}"
                )

                if len(results) >= max_results:
                    break

    if not results:
        return f"没有在 {path} 中找到关键词：{keyword}"

    return "\n".join(results)

def edit_file(path: str, old_text: str, new_text: str) -> str:
    """
    修改 workspace 中指定文件的文本内容。

    工作方式：
    1. 读取文件内容
    2. 查找 old_text
    3. 如果找到，就替换成 new_text
    4. 写回文件
    5. 返回修改 diff

    注意：
    - 只能修改 workspace 目录内的文件
    - old_text 必须精确匹配
    - 不允许修改 .env 等敏感文件
    """
    target_path = safe_resolve_path(path)

    if not target_path.exists():
        return f"文件不存在：{path}"

    if not target_path.is_file():
        return f"这不是一个文件：{path}"

    if target_path.name == ".env" or ".env" in target_path.parts:
        return "禁止修改 .env 等敏感配置文件"

    allowed_suffixes = {
        ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".html", ".css", ".js"
    }

    if target_path.suffix not in allowed_suffixes:
        return f"不支持修改该类型文件：{target_path.suffix}"

    max_size = 100_000
    file_size = target_path.stat().st_size

    if file_size > max_size:
        return f"文件过大，当前大小 {file_size} 字节，超过限制 {max_size} 字节"

    try:
        original_content = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"文件编码不是 utf-8，暂时无法修改：{path}"

    if old_text not in original_content:
        return (
            "没有找到要替换的 old_text，未进行修改。\n"
            "请先使用 read_file 查看文件内容，确保 old_text 与原文完全一致。"
        )

    new_content = original_content.replace(old_text, new_text, 1)

    backup_path = target_path.with_suffix(target_path.suffix + ".bak")
    backup_path.write_text(original_content, encoding="utf-8")

    target_path.write_text(new_content, encoding="utf-8")

    diff = difflib.unified_diff(
        original_content.splitlines(),
        new_content.splitlines(),
        fromfile=f"{path} 原始版本",
        tofile=f"{path} 修改后版本",
        lineterm=""
    )

    diff_text = "\n".join(diff)

    return (
        f"文件修改成功：{path}\n"
        f"已创建备份文件：{backup_path.relative_to(WORKSPACE_ROOT)}\n\n"
        f"修改 diff：\n{diff_text}"
    )


def write_new_file(path: str, content: str) -> str:
    """
    在 workspace 中创建一个新文件。

    注意：
    - 只能创建 workspace 内的文件
    - 不允许覆盖已有文件
    - 不允许创建 .env 等敏感文件
    - 只允许创建常见文本/代码文件
    """
    target_path = safe_resolve_path(path)

    # 禁止创建敏感文件
    if target_path.name == ".env" or ".env" in target_path.parts:
        return "禁止创建 .env 文件。"

    # 禁止创建隐藏文件，但允许 .gitignore
    if target_path.name.startswith(".") and target_path.name != ".gitignore":
        return f"禁止创建隐藏文件：{path}"

    # 不允许覆盖已有文件
    if target_path.exists():
        return f"文件已存在，拒绝覆盖：{path}"

    # 父目录必须存在
    if not target_path.parent.exists():
        return f"父目录不存在：{target_path.parent.relative_to(WORKSPACE_ROOT)}"

    if not target_path.parent.is_dir():
        return f"父路径不是目录：{target_path.parent.relative_to(WORKSPACE_ROOT)}"

    allowed_suffixes = {
        ".py",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".html",
        ".css",
        ".js",
    }

    if target_path.suffix not in allowed_suffixes and target_path.name != ".gitignore":
        return f"不支持创建该类型文件：{target_path.suffix}"

    max_content_size = 50_000

    if len(content) > max_content_size:
        return f"文件内容过大，拒绝创建。最大允许 {max_content_size} 字符。"

    try:
        target_path.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"创建文件失败：{str(e)}"

    return (
        f"文件创建成功。\n"
        f"路径：{target_path.relative_to(WORKSPACE_ROOT)}\n"
        f"字符数：{len(content)}"
    )


def is_command_allowed(command_parts: list[str]) -> bool:
    """
    检查命令是否在安全白名单中。

    当前只允许：
    1. python --version
    2. python -m py_compile ...
    3. python -m pytest ...
    4. pytest ...
    """
    if not command_parts:
        return False

    # 禁止明显危险字符
    dangerous_tokens = {"&&", "||", ";", "|", ">", "<", "`"}

    for part in command_parts:
        if part in dangerous_tokens:
            return False

        # 禁止访问上级目录或敏感文件
        if ".." in part:
            return False

        if ".env" in part:
            return False

        # 禁止 Windows 绝对盘符路径，例如 C:\xxx
        if ":" in part:
            return False

    # python --version
    if command_parts == ["python", "--version"]:
        return True

    # python -m py_compile xxx.py
    if len(command_parts) >= 4 and command_parts[0:3] == ["python", "-m", "py_compile"]:
        return True

    # python -m pytest ...
    if len(command_parts) >= 3 and command_parts[0:3] == ["python", "-m", "pytest"]:
        return True

    # pytest ...
    if command_parts[0] == "pytest":
        return True

    return False


def run_command(command: str, cwd: str = ".") -> dict[str, Any]:
    """
    在 workspace 内运行安全白名单命令。

    返回结构化结果：
    - success: bool
    - result: str
    - error: dict | None
    """
    if not command or not command.strip():
        return make_tool_error(
            error_type="command_not_allowed",
            message="命令不能为空。",
            detail=command,
        )

    command_parts = command.strip().split()

    if not command_parts:
        return make_tool_error(
            error_type="command_not_allowed",
            message="命令不能为空。",
            detail=command,
        )

    if not is_command_allowed(command_parts):
        return make_tool_error(
            error_type="command_not_allowed",
            message="命令不在白名单中，已拒绝执行。",
            detail=command,
            result=(
                "命令被安全策略拒绝。\n"
                f"命令：{command}\n"
                "当前只允许执行安全白名单命令，例如：\n"
                "- python --version\n"
                "- python -m py_compile <file>\n"
                "- python -m pytest\n"
                "- pytest"
            ),
        )

    try:
        target_cwd = safe_resolve_path(cwd)
    except Exception as e:
        return make_tool_error(
            error_type="path_forbidden",
            message="工作目录路径被安全策略拒绝。",
            detail=str(e),
            result=f"工作目录不安全，拒绝执行命令：{cwd}",
        )

    if not target_cwd.exists():
        return make_tool_error(
            error_type="file_not_found",
            message="工作目录不存在。",
            detail=cwd,
            result=f"工作目录不存在：{cwd}",
        )

    if not target_cwd.is_dir():
        return make_tool_error(
            error_type="path_forbidden",
            message="工作目录不是目录。",
            detail=cwd,
            result=f"工作目录不是目录：{cwd}",
        )

    try:
        completed = subprocess.run(
            command_parts,
            cwd=target_cwd,
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
    except FileNotFoundError as e:
        return make_tool_error(
            error_type="command_failed",
            message="命令程序不存在。",
            detail=str(e),
            result=f"命令程序不存在，无法执行：{command_parts[0]}",
        )
    except subprocess.TimeoutExpired:
        return make_tool_error(
            error_type="command_timeout",
            message="命令执行超时，已终止。",
            detail=command,
            result=f"命令执行超时，已终止：{command}",
        )
    except Exception as e:
        return make_tool_error(
            error_type="command_failed",
            message="命令执行时发生异常。",
            detail=str(e),
            result=f"命令执行时发生异常：{str(e)}",
        )

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()

    result_text = (
        f"命令：{command}\n"
        f"工作目录：{cwd}\n"
        f"退出码 returncode：{completed.returncode}\n\n"
        f"标准输出 stdout：\n{stdout if stdout else '[无]'}\n\n"
        f"错误输出 stderr：\n{stderr if stderr else '[无]'}"
    )

    if completed.returncode != 0:
        return make_tool_error(
            error_type="command_failed",
            message="命令执行失败，退出码不为 0。",
            detail=stderr or stdout or f"returncode={completed.returncode}",
            result=result_text,
        )

    return make_tool_success(result_text)


def get_file_diff(path: str) -> str:
    """
    查看某个文件相对于 .bak 备份文件的修改差异。

    例如：
    - 当前文件：demo_project/main.py
    - 备份文件：demo_project/main.py.bak

    返回 unified diff。
    """
    target_path = safe_resolve_path(path)

    if not target_path.exists():
        return f"文件不存在：{path}"

    if not target_path.is_file():
        return f"这不是一个文件：{path}"

    backup_path = target_path.with_suffix(target_path.suffix + ".bak")

    if not backup_path.exists():
        return (
            f"没有找到备份文件：{backup_path.relative_to(WORKSPACE_ROOT)}\n"
            "暂时无法生成 diff。请先通过 edit_file 修改该文件。"
        )

    try:
        old_content = backup_path.read_text(encoding="utf-8")
        new_content = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"文件编码不是 utf-8，暂时无法生成 diff：{path}"

    if old_content == new_content:
        return f"文件没有变化：{path}"

    diff = difflib.unified_diff(
        old_content.splitlines(),
        new_content.splitlines(),
        fromfile=str(backup_path.relative_to(WORKSPACE_ROOT)),
        tofile=str(target_path.relative_to(WORKSPACE_ROOT)),
        lineterm=""
    )

    diff_text = "\n".join(diff)

    max_chars = 8000
    if len(diff_text) > max_chars:
        diff_text = diff_text[:max_chars] + "\n\n[diff 过长，已截断]"

    return diff_text


def get_workspace_diff() -> str:
    """
    查看 workspace 中所有存在 .bak 备份文件的修改差异。

    工作方式：
    1. 遍历 workspace 下的普通文件
    2. 查找对应的 .bak 文件
    3. 如果当前文件和 .bak 不同，就生成 diff
    """
    ensure_workspace_exists()

    diffs = []
    max_total_chars = 12000

    for file_path in WORKSPACE_ROOT.rglob("*"):
        if should_ignore_path(file_path):
            continue

        if any(should_ignore_path(parent) for parent in file_path.parents):
            continue

        if not file_path.is_file():
            continue

        backup_path = file_path.with_suffix(file_path.suffix + ".bak")

        if not backup_path.exists():
            continue

        try:
            old_content = backup_path.read_text(encoding="utf-8")
            new_content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        if old_content == new_content:
            continue

        relative_file = file_path.relative_to(WORKSPACE_ROOT)
        relative_backup = backup_path.relative_to(WORKSPACE_ROOT)

        diff = difflib.unified_diff(
            old_content.splitlines(),
            new_content.splitlines(),
            fromfile=str(relative_backup),
            tofile=str(relative_file),
            lineterm=""
        )

        diff_text = "\n".join(diff)

        if diff_text:
            diffs.append(diff_text)

        current_total = sum(len(item) for item in diffs)
        if current_total > max_total_chars:
            diffs.append("\n[workspace diff 过长，已截断]")
            break

    if not diffs:
        return "当前 workspace 中没有发现可对比的修改。"

    return "\n\n".join(diffs)


def get_git_status(cwd: str = ".") -> str:
    """
    查看 workspace 中某个 Git 仓库的工作区状态。

    相当于执行：
    git status --short

    参数：
    - cwd：相对于 workspace 的 Git 仓库目录，例如 demo_project
    """
    target_cwd = safe_resolve_path(cwd)

    if not target_cwd.exists():
        return f"工作目录不存在：{cwd}"

    if not target_cwd.is_dir():
        return f"工作目录不是目录：{cwd}"

    try:
        completed = subprocess.run(
            ["git", "status", "--short"],
            cwd=target_cwd,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
    except FileNotFoundError:
        return "Git 命令不存在，请先确认电脑已安装 Git，并且 git 可以在终端中使用。"
    except subprocess.TimeoutExpired:
        return "git status 执行超时，已终止。"

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()

    if completed.returncode != 0:
        return (
            "git status 执行失败。\n"
            f"工作目录：{cwd}\n"
            f"退出码：{completed.returncode}\n"
            f"错误输出：\n{stderr if stderr else '[无]'}"
        )

    if not stdout:
        return "Git 工作区干净：没有未提交修改。"

    return (
        f"Git 工作区状态，目录：{cwd}\n\n"
        f"{stdout}"
    )


def get_git_diff(cwd: str = ".", path: str = ".") -> str:
    """
    查看 workspace 中某个 Git 仓库的修改差异。

    相当于执行：
    git diff -- path

    参数：
    - cwd：相对于 workspace 的 Git 仓库目录，例如 demo_project
    - path：相对于 cwd 的文件或目录路径，例如 . 或 main.py
    """
    target_cwd = safe_resolve_path(cwd)

    if not target_cwd.exists():
        return f"工作目录不存在：{cwd}"

    if not target_cwd.is_dir():
        return f"工作目录不是目录：{cwd}"

    # 路径安全检查：path 不能跳出 cwd
    target_path = (target_cwd / path).resolve()

    try:
        target_path.relative_to(target_cwd)
    except ValueError:
        return "禁止查看 Git 仓库目录之外的 diff。"

    if ".." in path or ".env" in path or ":" in path:
        return "路径被安全策略拒绝。"

    try:
        completed = subprocess.run(
            ["git", "diff", "--", path],
            cwd=target_cwd,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
    except FileNotFoundError:
        return "Git 命令不存在，请先确认电脑已安装 Git，并且 git 可以在终端中使用。"
    except subprocess.TimeoutExpired:
        return "git diff 执行超时，已终止。"

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()

    if completed.returncode != 0:
        return (
            "git diff 执行失败。\n"
            f"工作目录：{cwd}\n"
            f"路径：{path}\n"
            f"退出码：{completed.returncode}\n"
            f"错误输出：\n{stderr if stderr else '[无]'}"
        )

    if not stdout:
        return "当前没有 Git diff 改动。"

    max_chars = 12000
    if len(stdout) > max_chars:
        stdout = stdout[:max_chars] + "\n\n[git diff 过长，已截断]"

    return stdout


def ensure_gitignore(cwd: str = ".", patterns: list[str] | None = None) -> str:
    """
    创建或更新指定项目目录下的 .gitignore 文件。

    参数：
    - cwd：相对于 workspace 的项目目录，例如 demo_project
    - patterns：需要写入 .gitignore 的忽略规则
    """
    target_cwd = safe_resolve_path(cwd)

    if not target_cwd.exists():
        return f"工作目录不存在：{cwd}"

    if not target_cwd.is_dir():
        return f"工作目录不是目录：{cwd}"

    if patterns is None:
        patterns = [
            "__pycache__/",
            "*.pyc",
            "*.pyo",
            "*.bak",
        ]

    # 基础安全过滤：不允许写入危险规则
    safe_patterns = []

    for pattern in patterns:
        pattern = str(pattern).strip()

        if not pattern:
            continue

        if ".." in pattern or ":" in pattern or "\\" in pattern:
            return f"忽略规则不安全，已拒绝：{pattern}"

        if pattern.startswith("/"):
            return f"忽略规则不允许使用绝对路径：{pattern}"

        safe_patterns.append(pattern)

    gitignore_path = target_cwd / ".gitignore"

    if gitignore_path.exists():
        old_content = gitignore_path.read_text(encoding="utf-8")
    else:
        old_content = ""

    existing_lines = {
        line.strip()
        for line in old_content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    missing_patterns = [
        pattern
        for pattern in safe_patterns
        if pattern not in existing_lines
    ]

    if not missing_patterns:
        return (
            f".gitignore 已存在，并且所有规则都已包含。\n"
            f"路径：{gitignore_path.relative_to(WORKSPACE_ROOT)}"
        )

    new_lines = []

    if old_content.strip():
        new_lines.append(old_content.rstrip())
        new_lines.append("")

    new_lines.append("# Generated by Mini Coding Agent")
    new_lines.extend(missing_patterns)

    new_content = "\n".join(new_lines) + "\n"

    gitignore_path.write_text(new_content, encoding="utf-8")

    return (
        f"已创建或更新 .gitignore。\n"
        f"路径：{gitignore_path.relative_to(WORKSPACE_ROOT)}\n"
        f"新增规则：\n"
        + "\n".join(f"- {pattern}" for pattern in missing_patterns)
    )


FILE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出 workspace 工作区中指定目录下的文件和文件夹。用于查看项目结构、查找文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于 workspace 的目录路径，例如 . 或 demo_project"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取 workspace 工作区中指定文件的文本内容。用于查看代码、README、配置文件等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于 workspace 的文件路径，例如 demo_project/README.md"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_lines",
            "description": "读取 workspace 中指定文件的指定行范围。当用户要求查看某几行代码、读取文件局部内容、查看关键词附近上下文时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于 workspace 的文件路径，例如 demo_project/main.py"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号，从 1 开始"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号，包含该行"
                    }
                },
                "required": ["path", "start_line", "end_line"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "在 workspace 工作区中递归搜索关键词。用于查找函数、接口、类名、变量名、配置项等代码位置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "要搜索的关键词，例如 hello、FastAPI、app.get"
                    },
                    "path": {
                        "type": "string",
                        "description": "相对于 workspace 的搜索目录，例如 . 或 demo_project"
                    }
                },
                "required": ["keyword", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "修改 workspace 工作区中的指定文本文件。通过 old_text 精确匹配旧内容，并替换为 new_text。用于小范围代码修改。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于 workspace 的文件路径，例如 demo_project/main.py"
                    },
                    "old_text": {
                        "type": "string",
                        "description": "需要被替换的旧文本，必须和文件中的内容完全一致"
                    },
                    "new_text": {
                        "type": "string",
                        "description": "替换后的新文本"
                    }
                },
                "required": ["path", "old_text", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_new_file",
            "description": "在 workspace 中创建一个新的文本或代码文件。当用户要求新建文件、创建代码文件、生成 README、生成配置文件时使用。该工具不允许覆盖已有文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于 workspace 的新文件路径，例如 demo_project/utils.py"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入新文件的完整内容"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在 workspace 工作区中运行安全白名单命令。用于执行 Python 语法检查、pytest 测试等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的命令，例如 python -m py_compile demo_project/main.py"
                    },
                    "cwd": {
                        "type": "string",
                        "description": "相对于 workspace 的工作目录，例如 . 或 demo_project"
                    }
                },
                "required": ["command", "cwd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_diff",
            "description": "查看 workspace 中某个文件相对于 .bak 备份文件的修改差异。用于审查单个文件改了什么。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于 workspace 的文件路径，例如 demo_project/main.py"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_workspace_diff",
            "description": "查看 workspace 中所有存在 .bak 备份的文件修改差异。用于审查整个工作区改动。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_git_status",
            "description": "查看 workspace 中指定 Git 仓库的工作区状态。用于了解哪些文件被修改、新增或删除。",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": "相对于 workspace 的 Git 仓库目录，例如 demo_project"
                    }
                },
                "required": ["cwd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_git_diff",
            "description": "查看 workspace 中指定 Git 仓库的 Git diff 修改差异。用于审查代码改动。",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": "相对于 workspace 的 Git 仓库目录，例如 demo_project"
                    },
                    "path": {
                        "type": "string",
                        "description": "相对于 Git 仓库目录的文件或目录路径，例如 . 或 main.py"
                    }
                },
                "required": ["cwd", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ensure_gitignore",
            "description": "创建或更新 workspace 中指定项目目录下的 .gitignore 文件，用于忽略 __pycache__、*.pyc、*.pyo、*.bak 等不应提交到 Git 的生成文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": "相对于 workspace 的项目目录，例如 demo_project"
                    },
                    "patterns": {
                        "type": "array",
                        "description": "需要加入 .gitignore 的忽略规则列表，例如 __pycache__/、*.bak",
                        "items": {
                            "type": "string"
                        }
                    }
                },
                "required": ["cwd"]
            }
        }
    }
]


AVAILABLE_FILE_TOOLS = {
    "list_files": list_files,
    "read_file": read_file,
    "read_file_lines": read_file_lines,
    "search_code": search_code,
    "edit_file": edit_file,
    "write_new_file": write_new_file,
    "run_command": run_command,
    "get_file_diff": get_file_diff,
    "get_workspace_diff": get_workspace_diff,
    "get_git_status": get_git_status,
    "get_git_diff": get_git_diff,
    "ensure_gitignore": ensure_gitignore,
}