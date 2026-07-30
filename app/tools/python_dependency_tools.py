import ast
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.tools import code_structure_tools
from app.tools import file_tools


# 最多分析的 Python 文件数量。
MAX_DEPENDENCY_FILES = 500

# 影响分析最多返回多少个文件。
MAX_IMPACT_RESULTS = 100

# 允许的最大影响层级。
MAX_IMPACT_DEPTH = 5


@dataclass(frozen=True)
class ImportReference:
    """
    表示一个已经成功映射到本地文件的 import。

    例如：

        from app.models import User

    可能映射到：

        app/models.py
    """

    target_module: str
    target_path: Path
    line_number: int
    import_text: str


def _path_is_ignored(path: Path) -> bool:
    """
    判断文件自身或它的父目录是否应该被忽略。

    例如：

        demo_project/__pycache__/test.py

    因为 __pycache__ 应该忽略，
    所以里面的文件也不能参与依赖分析。
    """

    relative_path = path.relative_to(
        file_tools.WORKSPACE_ROOT
    )

    current_path = file_tools.WORKSPACE_ROOT

    for part in relative_path.parts:
        current_path = current_path / part

        if file_tools.should_ignore_path(
            current_path
        ):
            return True

    return False


def _validate_project_paths(
    *,
    path: str,
    project_root: str,
) -> tuple[Path, Path] | dict[str, Any]:
    """
    统一检查目标文件和项目根目录。

    返回成功时：

        (target_path, project_root_path)

    返回失败时：

        结构化工具错误字典
    """

    try:
        target_path = (
            file_tools.safe_resolve_path(path)
        )

        project_root_path = (
            file_tools.safe_resolve_path(
                project_root
            )
        )

    except ValueError as error:
        return file_tools.make_tool_error(
            error_type="path_forbidden",
            message="路径被安全策略拒绝。",
            detail=str(error),
        )

    if not project_root_path.exists():
        return file_tools.make_tool_error(
            error_type="file_not_found",
            message=(
                "项目根目录不存在："
                f"{project_root}"
            ),
        )

    if not project_root_path.is_dir():
        return file_tools.make_tool_error(
            error_type="validation_error",
            message=(
                "project_root 必须是目录："
                f"{project_root}"
            ),
        )

    if not target_path.exists():
        return file_tools.make_tool_error(
            error_type="file_not_found",
            message=f"目标文件不存在：{path}",
        )

    if not target_path.is_file():
        return file_tools.make_tool_error(
            error_type="validation_error",
            message=f"目标不是文件：{path}",
        )

    if target_path.suffix.lower() != ".py":
        return file_tools.make_tool_error(
            error_type="unsupported_file_type",
            message=(
                "只支持分析 Python 文件："
                f"{path}"
            ),
        )

    try:
        target_path.relative_to(
            project_root_path
        )
    except ValueError:
        return file_tools.make_tool_error(
            error_type="path_forbidden",
            message=(
                "目标文件不在指定项目根目录中。"
            ),
            detail=(
                f"目标文件：{path}；"
                f"项目根目录：{project_root}"
            ),
        )

    if _path_is_ignored(target_path):
        return file_tools.make_tool_error(
            error_type="path_forbidden",
            message=(
                "目标文件位于忽略目录中，"
                "禁止分析。"
            ),
        )

    return target_path, project_root_path


def _module_name_for_file(
    file_path: Path,
    project_root: Path,
) -> str:
    """
    根据项目内的相对路径生成 Python 模块名。

    例如：

        project_root/app/services/user_service.py

    转换为：

        app.services.user_service

    对于：

        app/services/__init__.py

    转换为：

        app.services
    """

    relative_path = file_path.relative_to(
        project_root
    )

    if relative_path.name == "__init__.py":
        module_parts = list(
            relative_path.parent.parts
        )
    else:
        module_parts = [
            *relative_path.parent.parts,
            relative_path.stem,
        ]

    return ".".join(module_parts)


def _current_package_name(
    file_path: Path,
    project_root: Path,
) -> str:
    """
    获取当前文件所在的 Python package 名称。

    例如文件：

        app/services/order_service.py

    模块名：

        app.services.order_service

    所在 package：

        app.services
    """

    module_name = _module_name_for_file(
        file_path,
        project_root,
    )

    if file_path.name == "__init__.py":
        return module_name

    if "." not in module_name:
        return ""

    return module_name.rsplit(".", 1)[0]


def _build_module_index(
    project_root: Path,
) -> tuple[
    dict[str, Path],
    bool,
]:
    """
    扫描项目中的 Python 文件并建立模块索引。

    返回：

        {
            "app.models": Path("app/models.py"),
            "app.services.user": Path(
                "app/services/user.py"
            ),
        }

    第二个返回值表示文件扫描是否被截断。
    """

    files, truncated = (
        code_structure_tools.iter_python_files(
            project_root
        )
    )

    files = files[:MAX_DEPENDENCY_FILES]

    if len(files) >= MAX_DEPENDENCY_FILES:
        truncated = True

    module_index: dict[str, Path] = {}

    for file_path in files:
        module_name = _module_name_for_file(
            file_path,
            project_root,
        )

        # 项目根目录中的 __init__.py
        # 可能得到空模块名，这里暂时跳过。
        if not module_name:
            continue

        module_index[module_name] = file_path

    return module_index, truncated


def _format_import_statement(
    node: ast.Import | ast.ImportFrom,
) -> str:
    """
    将 AST import 节点恢复为可读代码。

    Python 3.9 以上提供 ast.unparse。
    """

    try:
        return ast.unparse(node)
    except Exception:
        return (
            "无法格式化的 import，"
            f"位于第 {node.lineno} 行"
        )


def _resolve_relative_base_module(
    *,
    node: ast.ImportFrom,
    current_package: str,
) -> str | None:
    """
    解析相对导入的基础模块名。

    例如当前文件位于：

        app.services.order_service

    当前 package 是：

        app.services

    导入：

        from .user_service import UserService

    level = 1，最终基础模块：

        app.services.user_service

    导入：

        from ..models import Order

    level = 2，最终基础模块：

        app.models
    """

    # 绝对导入，例如：
    # from app.models import User
    if node.level == 0:
        return node.module or ""

    package_parts = (
        current_package.split(".")
        if current_package
        else []
    )

    # 单点表示当前 package，不向上移动。
    # 双点表示向上移动一级。
    levels_to_climb = node.level - 1

    if levels_to_climb > len(package_parts):
        return None

    if levels_to_climb:
        base_parts = package_parts[
            :-levels_to_climb
        ]
    else:
        base_parts = package_parts

    if node.module:
        base_parts = [
            *base_parts,
            *node.module.split("."),
        ]

    return ".".join(base_parts)


def _collect_import_nodes(
    tree: ast.Module,
) -> list[
    ast.Import | ast.ImportFrom
]:
    """
    收集文件中的全部 import 节点，
    并按行号排序。
    """

    nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(
            node,
            (
                ast.Import,
                ast.ImportFrom,
            ),
        )
    ]

    return sorted(
        nodes,
        key=lambda node: int(
            getattr(node, "lineno", 0)
        ),
    )


def _resolve_file_dependencies(
    *,
    source_file: Path,
    project_root: Path,
    module_index: dict[str, Path],
) -> tuple[
    dict[Path, list[ImportReference]],
    list[str],
]:
    """
    解析一个 Python 文件的 import。

    返回：

    1. 已成功映射到项目文件的本地依赖；
    2. 未映射到本地文件的 import。

    第二类可能是：
    - Python 标准库；
    - 第三方依赖；
    - 动态路径；
    - 暂时无法静态判断的导入。
    """

    tree, _, _ = (
        code_structure_tools.parse_python_file(
            source_file
        )
    )

    current_package = (
        _current_package_name(
            source_file,
            project_root,
        )
    )

    local_dependencies: dict[
        Path,
        list[ImportReference],
    ] = {}

    non_local_imports: list[str] = []

    for node in _collect_import_nodes(tree):
        import_text = (
            _format_import_statement(node)
        )

        resolved_any = False

        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_module = alias.name

                target_path = module_index.get(
                    imported_module
                )

                if target_path is None:
                    continue

                reference = ImportReference(
                    target_module=imported_module,
                    target_path=target_path,
                    line_number=node.lineno,
                    import_text=import_text,
                )

                local_dependencies.setdefault(
                    target_path,
                    [],
                ).append(reference)

                resolved_any = True

        else:
            base_module = (
                _resolve_relative_base_module(
                    node=node,
                    current_package=(
                        current_package
                    ),
                )
            )

            if base_module is not None:
                for alias in node.names:
                    # from x import *
                    if alias.name == "*":
                        candidate_module = (
                            base_module
                        )
                    elif base_module:
                        candidate_module = (
                            f"{base_module}."
                            f"{alias.name}"
                        )
                    else:
                        candidate_module = (
                            alias.name
                        )

                    # 情况一：
                    # from app.services import user_service
                    #
                    # candidate_module：
                    # app.services.user_service
                    target_module = (
                        candidate_module
                    )

                    target_path = module_index.get(
                        candidate_module
                    )

                    # 情况二：
                    # from app.models import User
                    #
                    # User 是 models.py 中的符号，
                    # 不是独立模块。
                    # 此时应该映射到 app.models。
                    if (
                        target_path is None
                        and base_module
                    ):
                        target_module = (
                            base_module
                        )

                        target_path = (
                            module_index.get(
                                base_module
                            )
                        )

                    if target_path is None:
                        continue

                    reference = ImportReference(
                        target_module=target_module,
                        target_path=target_path,
                        line_number=node.lineno,
                        import_text=import_text,
                    )

                    local_dependencies.setdefault(
                        target_path,
                        [],
                    ).append(reference)

                    resolved_any = True

        if not resolved_any:
            non_local_imports.append(
                f"第 {node.lineno} 行："
                f"{import_text}"
            )

    return (
        local_dependencies,
        non_local_imports,
    )


def get_python_dependencies(
    path: str,
    project_root: str = ".",
) -> dict[str, Any]:
    """
    分析一个 Python 文件的 import 依赖。

    返回：
    - 当前模块；
    - 本地项目依赖；
    - 未映射到项目文件的 import。
    """

    validated = _validate_project_paths(
        path=path,
        project_root=project_root,
    )

    if isinstance(validated, dict):
        return validated

    target_path, project_root_path = (
        validated
    )

    try:
        module_index, files_truncated = (
            _build_module_index(
                project_root_path
            )
        )

        (
            local_dependencies,
            non_local_imports,
        ) = _resolve_file_dependencies(
            source_file=target_path,
            project_root=project_root_path,
            module_index=module_index,
        )

    except ValueError as error:
        return file_tools.make_tool_error(
            error_type="python_parse_error",
            message="Python 依赖解析失败。",
            detail=str(error),
            result=str(error),
        )

    current_module = _module_name_for_file(
        target_path,
        project_root_path,
    )

    relative_target = (
        target_path.relative_to(
            file_tools.WORKSPACE_ROOT
        )
    )

    path_to_module = {
        module_path: module_name
        for module_name, module_path
        in module_index.items()
    }

    output = [
        f"目标文件：{relative_target}",
        (
            "当前模块："
            f"{current_module or '[项目根模块]'}"
        ),
        (
            "本地依赖数量："
            f"{len(local_dependencies)}"
        ),
        "",
        "本地项目依赖：",
    ]

    if local_dependencies:
        for dependency_path in sorted(
            local_dependencies,
            key=lambda item: str(item),
        ):
            dependency_relative = (
                dependency_path.relative_to(
                    file_tools.WORKSPACE_ROOT
                )
            )

            dependency_module = (
                path_to_module.get(
                    dependency_path,
                    "unknown",
                )
            )

            output.append(
                f"- {dependency_relative} "
                f"[模块：{dependency_module}]"
            )

            for reference in (
                local_dependencies[
                    dependency_path
                ]
            ):
                output.append(
                    f"  第 "
                    f"{reference.line_number} 行："
                    f"{reference.import_text}"
                )
    else:
        output.append("- 未发现本地项目依赖")

    output.extend(
        [
            "",
            (
                "未映射到项目文件的导入"
                "（可能是标准库、第三方库"
                "或无法静态解析的导入）："
            ),
        ]
    )

    if non_local_imports:
        output.extend(
            f"- {item}"
            for item in non_local_imports
        )
    else:
        output.append("- 无")

    if files_truncated:
        output.extend(
            [
                "",
                (
                    f"[项目 Python 文件超过 "
                    f"{MAX_DEPENDENCY_FILES} 个，"
                    "本次只分析前面的文件]"
                ),
            ]
        )

    return file_tools.make_tool_success(
        "\n".join(output)
    )


def _build_reverse_dependency_graph(
    *,
    project_root: Path,
) -> tuple[
    dict[Path, set[Path]],
    dict[str, Path],
    list[str],
    bool,
]:
    """
    建立反向依赖图。

    例如：

        api.py → service.py → models.py

    正向关系是：

        api.py 依赖 service.py
        service.py 依赖 models.py

    反向关系是：

        models.py 被 service.py 依赖
        service.py 被 api.py 依赖

    返回的图结构：

        {
            models.py: {service.py},
            service.py: {api.py},
        }
    """

    module_index, files_truncated = (
        _build_module_index(project_root)
    )

    reverse_graph: dict[
        Path,
        set[Path],
    ] = {}

    parse_warnings: list[str] = []

    source_files = sorted(
        set(module_index.values()),
        key=lambda item: str(item),
    )

    for source_file in source_files:
        try:
            (
                local_dependencies,
                _,
            ) = _resolve_file_dependencies(
                source_file=source_file,
                project_root=project_root,
                module_index=module_index,
            )

        except ValueError as error:
            relative_path = (
                source_file.relative_to(
                    file_tools.WORKSPACE_ROOT
                )
            )

            parse_warnings.append(
                f"{relative_path}：{error}"
            )
            continue

        for dependency_path in (
            local_dependencies
        ):
            reverse_graph.setdefault(
                dependency_path,
                set(),
            ).add(source_file)

    return (
        reverse_graph,
        module_index,
        parse_warnings,
        files_truncated,
    )


def analyze_python_impact(
    path: str,
    project_root: str = ".",
    max_depth: int = 3,
) -> dict[str, Any]:
    """
    分析一个 Python 文件的反向依赖影响范围。

    第 1 层：
    直接导入目标文件的文件。

    第 2 层：
    导入第 1 层文件的文件。

    依此类推。
    """

    if max_depth < 1:
        return file_tools.make_tool_error(
            error_type="validation_error",
            message=(
                "max_depth 必须大于等于 1。"
            ),
        )

    if max_depth > MAX_IMPACT_DEPTH:
        max_depth = MAX_IMPACT_DEPTH

    validated = _validate_project_paths(
        path=path,
        project_root=project_root,
    )

    if isinstance(validated, dict):
        return validated

    target_path, project_root_path = (
        validated
    )

    try:
        (
            reverse_graph,
            module_index,
            parse_warnings,
            files_truncated,
        ) = _build_reverse_dependency_graph(
            project_root=project_root_path
        )

    except ValueError as error:
        return file_tools.make_tool_error(
            error_type="python_parse_error",
            message="Python 影响分析失败。",
            detail=str(error),
            result=str(error),
        )

    path_to_module = {
        module_path: module_name
        for module_name, module_path
        in module_index.items()
    }

    impact_levels: dict[
        int,
        list[Path],
    ] = {}

    visited: set[Path] = {
        target_path
    }

    queue: deque[
        tuple[Path, int]
    ] = deque(
        [
            (target_path, 0),
        ]
    )

    total_results = 0
    results_truncated = False

    while queue:
        current_path, current_depth = (
            queue.popleft()
        )

        if current_depth >= max_depth:
            continue

        dependents = sorted(
            reverse_graph.get(
                current_path,
                set(),
            ),
            key=lambda item: str(item),
        )

        for dependent_path in dependents:
            if dependent_path in visited:
                continue

            visited.add(dependent_path)

            next_depth = current_depth + 1

            impact_levels.setdefault(
                next_depth,
                [],
            ).append(dependent_path)

            total_results += 1

            if (
                total_results
                >= MAX_IMPACT_RESULTS
            ):
                results_truncated = True
                break

            queue.append(
                (
                    dependent_path,
                    next_depth,
                )
            )

        if results_truncated:
            break

    target_relative = (
        target_path.relative_to(
            file_tools.WORKSPACE_ROOT
        )
    )

    target_module = path_to_module.get(
        target_path,
        _module_name_for_file(
            target_path,
            project_root_path,
        ),
    )

    output = [
        f"目标文件：{target_relative}",
        (
            "目标模块："
            f"{target_module or '[项目根模块]'}"
        ),
        f"最大分析层级：{max_depth}",
        (
            "可能受影响文件数量："
            f"{total_results}"
        ),
        "",
        (
            "影响范围"
            "（基于静态 import 关系）："
        ),
    ]

    if not impact_levels:
        output.append(
            "- 未发现其他 Python 文件"
            "导入该目标文件。"
        )

    for depth in sorted(
        impact_levels
    ):
        if depth == 1:
            level_name = "直接影响"
        else:
            level_name = "间接影响"

        output.extend(
            [
                "",
                (
                    f"第 {depth} 层"
                    f"（{level_name}）："
                ),
            ]
        )

        for dependent_path in (
            impact_levels[depth]
        ):
            dependent_relative = (
                dependent_path.relative_to(
                    file_tools.WORKSPACE_ROOT
                )
            )

            dependent_module = (
                path_to_module.get(
                    dependent_path,
                    "unknown",
                )
            )

            output.append(
                f"- {dependent_relative} "
                f"[模块：{dependent_module}]"
            )

    output.extend(
        [
            "",
            (
                "[说明] 以上结果只表示"
                "静态 import 依赖关系，"
                "不代表运行时一定会受到影响。"
            ),
        ]
    )

    if files_truncated:
        output.append(
            (
                f"[项目 Python 文件超过 "
                f"{MAX_DEPENDENCY_FILES} 个，"
                "本次只分析前面的文件]"
            )
        )

    if results_truncated:
        output.append(
            (
                f"[影响结果超过 "
                f"{MAX_IMPACT_RESULTS} 个，"
                "本次只返回前面的结果]"
            )
        )

    if parse_warnings:
        output.extend(
            [
                "",
                "以下文件解析失败：",
                *[
                    f"- {warning}"
                    for warning
                    in parse_warnings[:10]
                ],
            ]
        )

    return file_tools.make_tool_success(
        "\n".join(output)
    )


PYTHON_DEPENDENCY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": (
                "get_python_dependencies"
            ),
            "description": (
                "分析一个 Python 文件的 import "
                "依赖，区分本地项目文件与标准库、"
                "第三方库或无法映射的导入。"
                "当用户询问某文件依赖哪些模块、"
                "导入了哪些项目文件时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "相对于 workspace 的 "
                            "Python 文件路径，例如 "
                            "demo_project/app/api.py"
                        ),
                    },
                    "project_root": {
                        "type": "string",
                        "description": (
                            "相对于 workspace 的项目"
                            "根目录，例如 demo_project"
                        ),
                        "default": ".",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": (
                "analyze_python_impact"
            ),
            "description": (
                "根据静态 Python import 关系，"
                "分析哪些文件直接或间接依赖目标"
                "文件。适合在修改代码前评估可能"
                "受影响的文件范围。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "要进行影响分析的 Python "
                            "文件路径，例如 "
                            "demo_project/app/models.py"
                        ),
                    },
                    "project_root": {
                        "type": "string",
                        "description": (
                            "相对于 workspace 的项目"
                            "根目录，例如 demo_project"
                        ),
                        "default": ".",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": (
                            "最大递归影响层级，建议 "
                            "1 到 3，最大为 5。"
                        ),
                        "default": 3,
                        "minimum": 1,
                        "maximum": 5,
                    },
                },
                "required": ["path"],
            },
        },
    },
]


AVAILABLE_PYTHON_DEPENDENCY_TOOLS = {
    "get_python_dependencies": (
        get_python_dependencies
    ),
    "analyze_python_impact": (
        analyze_python_impact
    ),
}