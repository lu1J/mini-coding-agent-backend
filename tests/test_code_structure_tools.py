from pathlib import Path

from app.tools import (
    code_structure_tools,
)
from app.tools import file_tools


def setup_workspace(
    monkeypatch,
    tmp_path: Path,
) -> Path:
    """
    为每个测试创建一个临时 workspace。

    不操作真实项目中的 workspace。
    """

    workspace = (
        tmp_path / "workspace"
    ).resolve()

    workspace.mkdir()

    monkeypatch.setattr(
        file_tools,
        "WORKSPACE_ROOT",
        workspace,
    )

    return workspace


def test_get_python_file_outline(
    monkeypatch,
    tmp_path: Path,
):
    """
    应该识别：
    - import
    - 普通函数
    - 类
    - 普通方法
    - 异步方法
    """

    workspace = setup_workspace(
        monkeypatch,
        tmp_path,
    )

    project = (
        workspace / "demo_project"
    )

    project.mkdir()

    source_file = (
        project / "service.py"
    )

    source_file.write_text(
        (
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            "def create_app():\n"
            "    return None\n"
            "\n"
            "class UserService:\n"
            "    def create_user(self):\n"
            "        return True\n"
            "\n"
            "    async def load_user(self):\n"
            "        return None\n"
        ),
        encoding="utf-8",
    )

    result = (
        code_structure_tools
        .get_python_file_outline(
            "demo_project/service.py"
        )
    )

    assert result["success"] is True

    text = result["result"]

    assert "import os" in text

    assert (
        "from pathlib import Path"
        in text
    )

    assert (
        "[function] create_app"
        in text
    )

    assert (
        "[class] UserService"
        in text
    )

    assert (
        "[method] "
        "UserService.create_user"
        in text
    )

    assert (
        "[async_method] "
        "UserService.load_user"
        in text
    )


def test_search_python_symbol_ignores_comments_and_strings(
    monkeypatch,
    tmp_path: Path,
):
    """
    搜索符号时，不应该把注释和字符串
    当成真正的类定义。
    """

    workspace = setup_workspace(
        monkeypatch,
        tmp_path,
    )

    project = (
        workspace / "demo_project"
    )

    project.mkdir()

    (
        project / "service.py"
    ).write_text(
        (
            "class UserService:\n"
            "    def create_user(self):\n"
            "        return True\n"
        ),
        encoding="utf-8",
    )

    (
        project / "other.py"
    ).write_text(
        (
            "# UserService 这里只是注释\n"
            "message = 'UserService'\n"
        ),
        encoding="utf-8",
    )

    result = (
        code_structure_tools
        .search_python_symbol(
            "UserService",
            "demo_project",
        )
    )

    assert result["success"] is True

    text = result["result"]

    assert (
        "[class] UserService"
        in text
    )

    assert "service.py" in text

    # other.py 只有注释和字符串，
    # 不应该成为符号结果。
    assert "other.py" not in text


def test_search_python_method(
    monkeypatch,
    tmp_path: Path,
):
    """
    应该能够搜索类中的方法。
    """

    workspace = setup_workspace(
        monkeypatch,
        tmp_path,
    )

    project = (
        workspace / "demo_project"
    )

    project.mkdir()

    (
        project / "service.py"
    ).write_text(
        (
            "class UserService:\n"
            "    def create_user(self):\n"
            "        return True\n"
        ),
        encoding="utf-8",
    )

    result = (
        code_structure_tools
        .search_python_symbol(
            "create_user",
            "demo_project",
        )
    )

    assert result["success"] is True

    assert (
        "[method] "
        "UserService.create_user"
        in result["result"]
    )


def test_nested_function_is_not_classified_as_method(
    monkeypatch,
    tmp_path: Path,
):
    """
    方法内部的函数应该识别为嵌套函数，
    不能错误识别为类方法。
    """

    workspace = setup_workspace(
        monkeypatch,
        tmp_path,
    )

    project = (
        workspace / "demo_project"
    )

    project.mkdir()

    (
        project / "service.py"
    ).write_text(
        (
            "class UserService:\n"
            "    def create_user(self):\n"
            "        def normalize_name():\n"
            "            return 'name'\n"
            "        return normalize_name()\n"
        ),
        encoding="utf-8",
    )

    result = (
        code_structure_tools
        .get_python_file_outline(
            "demo_project/service.py"
        )
    )

    assert result["success"] is True

    text = result["result"]

    assert (
        "[method] "
        "UserService.create_user"
        in text
    )

    assert (
        "[nested_function] "
        "UserService.create_user."
        "normalize_name"
        in text
    )


def test_outline_returns_structured_parse_error(
    monkeypatch,
    tmp_path: Path,
):
    """
    Python 代码有语法错误时，
    应返回结构化错误。
    """

    workspace = setup_workspace(
        monkeypatch,
        tmp_path,
    )

    project = (
        workspace / "demo_project"
    )

    project.mkdir()

    (
        project / "broken.py"
    ).write_text(
        "def broken(\n",
        encoding="utf-8",
    )

    result = (
        code_structure_tools
        .get_python_file_outline(
            "demo_project/broken.py"
        )
    )

    assert result["success"] is False

    assert (
        result["error"]["type"]
        == "python_parse_error"
    )

    assert (
        "语法错误"
        in result["result"]
    )


def test_symbol_search_rejects_workspace_escape(
    monkeypatch,
    tmp_path: Path,
):
    """
    工具不能通过 ../ 跳出 workspace。
    """

    setup_workspace(
        monkeypatch,
        tmp_path,
    )

    result = (
        code_structure_tools
        .search_python_symbol(
            "secret",
            "../",
        )
    )

    assert result["success"] is False

    assert (
        result["error"]["type"]
        == "path_forbidden"
    )