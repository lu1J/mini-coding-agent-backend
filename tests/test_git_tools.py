import subprocess
import uuid
from pathlib import Path

from app.tools import file_tools


def run_git_command(args: list[str], cwd: Path):
    """
    测试内部使用的 Git 命令。
    注意：这是测试代码，不是 Agent 工具。
    """
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )


def create_test_git_repo() -> tuple[str, Path]:
    """
    在 workspace 中创建一个独立的临时 Git 仓库。

    注意：
    - 每次测试都创建唯一目录
    - 不复用 test_git_project
    - 避免 Windows 删除 .git 目录时出现 PermissionError
    """
    repo_name = f"test_git_project_{uuid.uuid4().hex[:8]}"
    repo_path = file_tools.safe_resolve_path(repo_name)

    repo_path.mkdir(parents=True, exist_ok=True)

    main_file = repo_path / "main.py"
    main_file.write_text("print('v1')\n", encoding="utf-8")

    result = run_git_command(["git", "init"], cwd=repo_path)
    assert result.returncode == 0, result.stderr

    result = run_git_command(["git", "config", "user.name", "Test User"], cwd=repo_path)
    assert result.returncode == 0, result.stderr

    result = run_git_command(["git", "config", "user.email", "test@example.com"], cwd=repo_path)
    assert result.returncode == 0, result.stderr

    result = run_git_command(["git", "add", "main.py"], cwd=repo_path)
    assert result.returncode == 0, result.stderr

    result = run_git_command(["git", "commit", "-m", "init"], cwd=repo_path)
    assert result.returncode == 0, result.stderr

    return repo_name, repo_path


def test_get_git_status_clean_repo():
    """
    get_git_status 应该能识别干净的 Git 工作区。
    """
    repo_name, _ = create_test_git_repo()

    result = file_tools.get_git_status(cwd=repo_name)

    assert "干净" in result or "没有未提交" in result


def test_get_git_status_modified_file():
    """
    修改已跟踪文件后，get_git_status 应该显示 M main.py。
    """
    repo_name, repo_path = create_test_git_repo()

    main_file = repo_path / "main.py"
    main_file.write_text("print('v2')\n", encoding="utf-8")

    result = file_tools.get_git_status(cwd=repo_name)

    assert "M main.py" in result or " M main.py" in result


def test_get_git_diff_modified_file():
    """
    修改已跟踪文件后，get_git_diff 应该返回 diff 内容。
    """
    repo_name, repo_path = create_test_git_repo()

    main_file = repo_path / "main.py"
    main_file.write_text("print('v2')\n", encoding="utf-8")

    result = file_tools.get_git_diff(
        cwd=repo_name,
        path="main.py",
    )

    assert "diff --git" in result
    assert "print('v1')" in result
    assert "print('v2')" in result


def test_get_git_status_not_git_repo():
    """
    非 Git 仓库调用 get_git_status 时，应该返回清晰提示，而不是崩溃。
    """
    folder_name = f"test_not_git_project_{uuid.uuid4().hex[:8]}"
    folder = file_tools.safe_resolve_path(folder_name)
    folder.mkdir(parents=True, exist_ok=True)

    result = file_tools.get_git_status(cwd=folder_name)

    assert "不是 Git 仓库" in result or "fatal" in result or "Git 状态获取失败" in result