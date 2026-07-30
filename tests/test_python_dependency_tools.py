from pathlib import Path

from app.tools import file_tools
from app.tools import python_dependency_tools


def setup_workspace(
    monkeypatch,
    tmp_path: Path,
) -> Path:
    """
    创建临时 workspace，
    避免测试污染真实项目。
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


def normalize_path_text(
    value: str,
) -> str:
    """
    Windows 使用反斜杠，
    Linux 使用正斜杠。

    测试中统一转换成正斜杠。
    """

    return value.replace("\\", "/")


def create_package_file(
    path: Path,
    content: str = "",
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        content,
        encoding="utf-8",
    )


def test_get_python_dependencies(
    monkeypatch,
    tmp_path: Path,
):
    """
    应该区分：
    - 本地项目依赖；
    - 标准库或第三方导入。
    """

    workspace = setup_workspace(
        monkeypatch,
        tmp_path,
    )

    project = (
        workspace / "demo_project"
    )

    create_package_file(
        project / "app" / "__init__.py"
    )

    create_package_file(
        (
            project
            / "app"
            / "services"
            / "__init__.py"
        )
    )

    create_package_file(
        (
            project
            / "app"
            / "services"
            / "user_service.py"
        ),
        (
            "class UserService:\n"
            "    pass\n"
        ),
    )

    create_package_file(
        project / "app" / "api.py",
        (
            "import json\n"
            "from app.services.user_service "
            "import UserService\n"
        ),
    )

    result = (
        python_dependency_tools
        .get_python_dependencies(
            path="demo_project/app/api.py",
            project_root="demo_project",
        )
    )

    assert result["success"] is True

    text = normalize_path_text(
        result["result"]
    )

    assert (
        "demo_project/app/services/"
        "user_service.py"
        in text
    )

    assert (
        "app.services.user_service"
        in text
    )

    assert "import json" in text


def test_relative_import_dependency(
    monkeypatch,
    tmp_path: Path,
):
    """
    应该解析相对导入：

        from .user_service import UserService
    """

    workspace = setup_workspace(
        monkeypatch,
        tmp_path,
    )

    project = (
        workspace / "demo_project"
    )

    create_package_file(
        project / "app" / "__init__.py"
    )

    create_package_file(
        (
            project
            / "app"
            / "services"
            / "__init__.py"
        )
    )

    create_package_file(
        (
            project
            / "app"
            / "services"
            / "user_service.py"
        ),
        "class UserService:\n    pass\n",
    )

    create_package_file(
        (
            project
            / "app"
            / "services"
            / "order_service.py"
        ),
        (
            "from .user_service "
            "import UserService\n"
        ),
    )

    result = (
        python_dependency_tools
        .get_python_dependencies(
            path=(
                "demo_project/app/services/"
                "order_service.py"
            ),
            project_root="demo_project",
        )
    )

    assert result["success"] is True

    text = normalize_path_text(
        result["result"]
    )

    assert (
        "demo_project/app/services/"
        "user_service.py"
        in text
    )


def test_analyze_python_impact(
    monkeypatch,
    tmp_path: Path,
):
    """
    依赖链：

        api.py
          ↓
        service.py
          ↓
        models.py

    修改 models.py 时：

        第 1 层：service.py
        第 2 层：api.py
    """

    workspace = setup_workspace(
        monkeypatch,
        tmp_path,
    )

    project = (
        workspace / "demo_project"
    )

    create_package_file(
        project / "app" / "__init__.py"
    )

    create_package_file(
        project / "app" / "models.py",
        "class User:\n    pass\n",
    )

    create_package_file(
        project / "app" / "service.py",
        (
            "from app.models import User\n"
            "\n"
            "def create_user():\n"
            "    return User()\n"
        ),
    )

    create_package_file(
        project / "app" / "api.py",
        (
            "from app.service "
            "import create_user\n"
        ),
    )

    result = (
        python_dependency_tools
        .analyze_python_impact(
            path=(
                "demo_project/app/models.py"
            ),
            project_root="demo_project",
            max_depth=3,
        )
    )

    assert result["success"] is True

    text = normalize_path_text(
        result["result"]
    )

    assert "第 1 层" in text
    assert "demo_project/app/service.py" in text

    assert "第 2 层" in text
    assert "demo_project/app/api.py" in text


def test_impact_analysis_rejects_file_outside_project_root(
    monkeypatch,
    tmp_path: Path,
):
    """
    目标文件必须位于指定 project_root 中。
    """

    workspace = setup_workspace(
        monkeypatch,
        tmp_path,
    )

    create_package_file(
        (
            workspace
            / "demo_project"
            / "app"
            / "main.py"
        ),
        "print('hello')\n",
    )

    create_package_file(
        (
            workspace
            / "other_project"
            / "outside.py"
        ),
        "print('outside')\n",
    )

    result = (
        python_dependency_tools
        .analyze_python_impact(
            path=(
                "other_project/outside.py"
            ),
            project_root=(
                "demo_project"
            ),
        )
    )

    assert result["success"] is False

    assert (
        result["error"]["type"]
        == "path_forbidden"
    )