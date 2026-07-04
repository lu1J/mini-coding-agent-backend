from pathlib import Path

import pytest

from app.tools import file_tools


def make_demo_file(relative_path: str, content: str) -> Path:
    """
    在 workspace 中创建测试文件。
    """
    target_path = file_tools.safe_resolve_path(relative_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    return target_path


def test_safe_resolve_path_allows_workspace_file():
    """
    safe_resolve_path 应该允许 workspace 内的相对路径。
    """
    path = file_tools.safe_resolve_path("demo_project/main.py")

    assert str(path).startswith(str(file_tools.WORKSPACE_ROOT))


def test_safe_resolve_path_blocks_parent_escape():
    """
    safe_resolve_path 应该禁止 ../ 路径越界。
    """
    with pytest.raises(ValueError):
        file_tools.safe_resolve_path("../outside.txt")


def test_read_file_lines_success():
    """
    read_file_lines 应该能读取指定行范围。
    """
    make_demo_file(
        "test_project/sample.py",
        "line1\nline2\nline3\nline4\n",
    )

    result = file_tools.read_file_lines(
        path="test_project/sample.py",
        start_line=2,
        end_line=3,
    )

    assert "读取范围：第 2 行到第 3 行" in result
    assert "2: line2" in result
    assert "3: line3" in result
    assert "1: line1" not in result


def test_read_file_lines_out_of_range():
    """
    read_file_lines 读取越界行号时，应该返回清晰提示，而不是崩溃。
    """
    make_demo_file(
        "test_project/out_of_range.py",
        "line1\nline2\n",
    )

    result = file_tools.read_file_lines(
        path="test_project/out_of_range.py",
        start_line=100,
        end_line=120,
    )

    assert "起始行超过文件总行数" in result
    assert "总行数：2" in result


def test_read_file_blocks_env_file():
    """
    read_file 不应该允许读取 .env 文件。
    """
    make_demo_file(
        ".env",
        "DEEPSEEK_API_KEY=your_deepseek_api_key_here",
    )

    result = file_tools.read_file(".env")

    assert "禁止读取 .env" in result


def test_search_code_finds_keyword():
    """
    search_code 应该能在代码中搜索关键词。
    """
    make_demo_file(
        "test_project/search_demo.py",
        "def hello():\n    return 'hi'\n",
    )

    result = file_tools.search_code(
        keyword="hello",
        path="test_project",
    )

    assert "search_demo.py:1" in result
    assert "def hello" in result


def test_write_new_file_success():
    """
    write_new_file 应该能创建不存在的新文件。
    """
    path = "test_project/new_file.py"

    target_path = file_tools.safe_resolve_path(path)
    if target_path.exists():
        target_path.unlink()

    result = file_tools.write_new_file(
        path=path,
        content="def add(a, b):\n    return a + b\n",
    )

    assert "文件创建成功" in result
    assert target_path.exists()
    assert "def add" in target_path.read_text(encoding="utf-8")


def test_write_new_file_refuses_overwrite():
    """
    write_new_file 不应该覆盖已有文件。
    """
    path = "test_project/existing.py"

    make_demo_file(
        path,
        "old content\n",
    )

    result = file_tools.write_new_file(
        path=path,
        content="new content\n",
    )

    target_path = file_tools.safe_resolve_path(path)
    content = target_path.read_text(encoding="utf-8")

    assert "文件已存在，拒绝覆盖" in result
    assert content == "old content\n"


def test_run_command_success_py_compile():
    """
    run_command 执行合法 py_compile 命令时应该成功。
    """
    make_demo_file(
        "test_project/compile_ok.py",
        "def ok():\n    return 1\n",
    )

    result = file_tools.run_command(
        command="python -m py_compile test_project/compile_ok.py",
        cwd=".",
    )

    assert result["success"] is True
    assert result["error"] is None
    assert "退出码 returncode：0" in result["result"]


def test_run_command_rejects_not_allowed_command():
    """
    run_command 应该拒绝不在白名单中的命令。
    """
    result = file_tools.run_command(
        command='python -c "print(1)"',
        cwd=".",
    )

    assert result["success"] is False
    assert result["error"]["type"] == "command_not_allowed"


def test_run_command_failed_for_missing_file():
    """
    run_command 对不存在文件执行 py_compile 时应该返回 command_failed。
    """
    result = file_tools.run_command(
        command="python -m py_compile test_project/not_exist.py",
        cwd=".",
    )

    assert result["success"] is False
    assert result["error"]["type"] == "command_failed"
    assert "退出码 returncode：1" in result["result"]