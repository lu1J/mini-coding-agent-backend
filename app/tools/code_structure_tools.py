import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.tools import file_tools


# 单个 Python 文件最大解析大小。
# 防止 Agent 一次解析特别大的文件，浪费内存和时间。
MAX_PYTHON_FILE_SIZE = 300_000

# 一次符号搜索最多扫描的 Python 文件数量。
MAX_PYTHON_FILES = 500

# 一次最多返回多少个符号结果。
MAX_SYMBOL_RESULTS = 50


@dataclass(frozen=True)
class PythonSymbol:
    """
    表示 Python 代码中的一个符号定义。

    例如：
    - class UserService
    - function create_app
    - method UserService.create_user
    """

    name: str
    qualified_name: str
    symbol_type: str
    line_start: int
    line_end: int


class PythonSymbolVisitor(ast.NodeVisitor):
    """
    遍历 Python AST，收集类、函数、异步函数和方法定义。

    scope_stack 用来记录当前所在作用域，例如：

    UserService -> create_user -> normalize_name
    """

    def __init__(self) -> None:
        self.symbols: list[PythonSymbol] = []

        # 每一项保存：
        # (作用域名称, 作用域类型)
        self.scope_stack: list[tuple[str, str]] = []

    def _qualified_name(self, name: str) -> str:
        """
        根据当前作用域生成限定名称。

        例如当前作用域是：
        UserService -> create_user

        新符号是：
        normalize_name

        最终得到：
        UserService.create_user.normalize_name
        """

        scope_names = [
            scope_name
            for scope_name, _ in self.scope_stack
        ]

        return ".".join([*scope_names, name])

    def _add_symbol(
        self,
        node: ast.AST,
        *,
        name: str,
        symbol_type: str,
    ) -> None:
        """
        把一个 AST 节点转换成 PythonSymbol。
        """

        line_start = int(
            getattr(node, "lineno", 0)
        )

        line_end = int(
            getattr(
                node,
                "end_lineno",
                line_start,
            )
            or line_start
        )

        self.symbols.append(
            PythonSymbol(
                name=name,
                qualified_name=self._qualified_name(
                    name
                ),
                symbol_type=symbol_type,
                line_start=line_start,
                line_end=line_end,
            )
        )

    def _function_type(
        self,
        *,
        is_async: bool,
    ) -> str:
        """
        根据当前作用域判断函数类型。

        顶层：
        function / async_function

        类内部：
        method / async_method

        函数内部：
        nested_function / async_nested_function
        """

        if not self.scope_stack:
            return (
                "async_function"
                if is_async
                else "function"
            )

        parent_type = self.scope_stack[-1][1]

        if parent_type == "class":
            return (
                "async_method"
                if is_async
                else "method"
            )

        return (
            "async_nested_function"
            if is_async
            else "nested_function"
        )

    def visit_ClassDef(
        self,
        node: ast.ClassDef,
    ) -> None:
        """
        遇到 class 定义时自动调用。
        """

        self._add_symbol(
            node,
            name=node.name,
            symbol_type="class",
        )

        # 进入这个类的作用域。
        self.scope_stack.append(
            (node.name, "class")
        )

        # 继续访问类内部的方法和嵌套类。
        self.generic_visit(node)

        # 离开类作用域。
        self.scope_stack.pop()

    def visit_FunctionDef(
        self,
        node: ast.FunctionDef,
    ) -> None:
        """
        遇到普通 def 时自动调用。
        """

        symbol_type = self._function_type(
            is_async=False
        )

        self._add_symbol(
            node,
            name=node.name,
            symbol_type=symbol_type,
        )

        self.scope_stack.append(
            (node.name, symbol_type)
        )

        self.generic_visit(node)

        self.scope_stack.pop()

    def visit_AsyncFunctionDef(
        self,
        node: ast.AsyncFunctionDef,
    ) -> None:
        """
        遇到 async def 时自动调用。
        """

        symbol_type = self._function_type(
            is_async=True
        )

        self._add_symbol(
            node,
            name=node.name,
            symbol_type=symbol_type,
        )

        self.scope_stack.append(
            (node.name, symbol_type)
        )

        self.generic_visit(node)

        self.scope_stack.pop()


def _path_is_ignored(path: Path) -> bool:
    """
    检查 path 本身或 workspace 内的任意父目录
    是否应该被忽略。

    例如：
    workspace/demo_project/__pycache__/test.py

    因为 __pycache__ 应该忽略，
    所以 test.py 也不能被分析。
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


def parse_python_file(
    target_path: Path,
) -> tuple[
    ast.Module,
    list[PythonSymbol],
    str,
]:
    """
    读取并解析 Python 文件。

    返回：
    1. AST 根节点；
    2. 符号列表；
    3. 原始源代码。
    """

    if (
        target_path.stat().st_size
        > MAX_PYTHON_FILE_SIZE
    ):
        raise ValueError(
            "Python 文件过大，拒绝解析："
            f"{target_path.name}，最大允许 "
            f"{MAX_PYTHON_FILE_SIZE} 字节"
        )

    try:
        source = target_path.read_text(
            encoding="utf-8"
        )
    except UnicodeDecodeError as error:
        raise ValueError(
            "文件不是 UTF-8 编码，无法解析"
        ) from error

    try:
        tree = ast.parse(
            source,
            filename=str(target_path),
        )
    except SyntaxError as error:
        line_number = (
            error.lineno
            or "未知"
        )

        raise ValueError(
            "Python 文件存在语法错误，无法解析："
            f"第 {line_number} 行，"
            f"{error.msg}"
        ) from error

    visitor = PythonSymbolVisitor()
    visitor.visit(tree)

    return tree, visitor.symbols, source


def format_import(
    node: ast.Import | ast.ImportFrom,
) -> str:
    """
    把 AST 中的 import 节点转换成可读文本。
    """

    names: list[str] = []

    for alias in node.names:
        if alias.asname:
            names.append(
                f"{alias.name} as "
                f"{alias.asname}"
            )
        else:
            names.append(alias.name)

    if isinstance(node, ast.Import):
        return (
            "import "
            + ", ".join(names)
        )

    module_name = node.module or ""

    # 相对导入中的点。
    # 例如 from ..services import user
    relative_prefix = "." * node.level

    return (
        f"from {relative_prefix}"
        f"{module_name} import "
        + ", ".join(names)
    )


def _collect_imports(
    tree: ast.Module,
) -> list[
    ast.Import | ast.ImportFrom
]:
    """
    收集文件中的全部 import，
    并按照代码行号排序。
    """

    imports = [
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
        imports,
        key=lambda node: int(
            getattr(node, "lineno", 0)
        ),
    )


def get_python_file_outline(
    path: str,
) -> dict[str, Any]:
    """
    获取单个 Python 文件的结构大纲。

    输出包括：
    - import；
    - 类；
    - 函数；
    - 方法；
    - 嵌套函数；
    - 起止行号。
    """

    try:
        target_path = (
            file_tools.safe_resolve_path(path)
        )
    except ValueError as error:
        return file_tools.make_tool_error(
            error_type="path_forbidden",
            message="路径被安全策略拒绝。",
            detail=str(error),
        )

    if not target_path.exists():
        return file_tools.make_tool_error(
            error_type="file_not_found",
            message=f"文件不存在：{path}",
        )

    if not target_path.is_file():
        return file_tools.make_tool_error(
            error_type="path_forbidden",
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

    if _path_is_ignored(target_path):
        return file_tools.make_tool_error(
            error_type="path_forbidden",
            message=(
                "该路径已被忽略，禁止分析："
                f"{path}"
            ),
        )

    try:
        tree, symbols, source = (
            parse_python_file(target_path)
        )
    except ValueError as error:
        return file_tools.make_tool_error(
            error_type="python_parse_error",
            message="Python 文件解析失败。",
            detail=str(error),
            result=str(error),
        )

    relative_path = (
        target_path.relative_to(
            file_tools.WORKSPACE_ROOT
        )
    )

    source_lines = source.splitlines()

    imports = _collect_imports(tree)

    output = [
        f"Python 文件：{relative_path}",
        f"总行数：{len(source_lines)}",
        f"Import 数量：{len(imports)}",
        f"符号数量：{len(symbols)}",
        "",
        "Imports：",
    ]

    if imports:
        for import_node in imports:
            output.append(
                f"- 第 "
                f"{import_node.lineno} 行："
                f"{format_import(import_node)}"
            )
    else:
        output.append("- 无")

    output.extend(
        [
            "",
            "Symbols：",
        ]
    )

    if symbols:
        for symbol in symbols:
            output.append(
                f"- [{symbol.symbol_type}] "
                f"{symbol.qualified_name} "
                f"（第 "
                f"{symbol.line_start}-"
                f"{symbol.line_end} 行）"
            )
    else:
        output.append(
            "- 未发现类或函数定义"
        )

    return file_tools.make_tool_success(
        "\n".join(output)
    )


def iter_python_files(
    target_path: Path,
) -> tuple[list[Path], bool]:
    """
    获取目标路径中的 Python 文件。

    返回：
    1. Python 文件列表；
    2. 是否因为数量限制而截断。
    """

    if target_path.is_file():
        if (
            target_path.suffix.lower()
            == ".py"
            and not _path_is_ignored(
                target_path
            )
        ):
            return [target_path], False

        return [], False

    files: list[Path] = []
    truncated = False

    for file_path in sorted(
        target_path.rglob("*.py")
    ):
        if not file_path.is_file():
            continue

        if _path_is_ignored(file_path):
            continue

        files.append(file_path)

        if len(files) >= MAX_PYTHON_FILES:
            truncated = True
            break

    return files, truncated


def search_python_symbol(
    query: str,
    path: str = ".",
) -> dict[str, Any]:
    """
    在 workspace 中搜索 Python 符号定义。

    与 search_code 不同：

    这里只搜索 AST 中真实存在的类、
    函数和方法定义。

    不会把注释、字符串或普通变量
    当作符号定义。
    """

    query = query.strip()

    if not query:
        return file_tools.make_tool_error(
            error_type="validation_error",
            message="query 不能为空。",
        )

    try:
        target_path = (
            file_tools.safe_resolve_path(path)
        )
    except ValueError as error:
        return file_tools.make_tool_error(
            error_type="path_forbidden",
            message="路径被安全策略拒绝。",
            detail=str(error),
        )

    if not target_path.exists():
        return file_tools.make_tool_error(
            error_type="file_not_found",
            message=f"路径不存在：{path}",
        )

    files, files_truncated = (
        iter_python_files(target_path)
    )

    if not files:
        return file_tools.make_tool_success(
            f"没有在 {path} 中找到"
            "可分析的 Python 文件。"
        )

    query_lower = query.lower()

    exact_matches: list[str] = []
    partial_matches: list[str] = []
    parse_warnings: list[str] = []

    for file_path in files:
        try:
            _, symbols, _ = (
                parse_python_file(file_path)
            )
        except ValueError as error:
            relative_path = (
                file_path.relative_to(
                    file_tools.WORKSPACE_ROOT
                )
            )

            parse_warnings.append(
                f"{relative_path}：{error}"
            )
            continue

        for symbol in symbols:
            symbol_name = (
                symbol.name.lower()
            )

            qualified_name = (
                symbol.qualified_name.lower()
            )

            if (
                query_lower
                not in symbol_name
                and query_lower
                not in qualified_name
            ):
                continue

            relative_path = (
                file_path.relative_to(
                    file_tools.WORKSPACE_ROOT
                )
            )

            match_text = (
                f"{relative_path}:"
                f"{symbol.line_start}-"
                f"{symbol.line_end} "
                f"[{symbol.symbol_type}] "
                f"{symbol.qualified_name}"
            )

            if (
                query_lower == symbol_name
                or query_lower
                == qualified_name
            ):
                exact_matches.append(
                    match_text
                )
            else:
                partial_matches.append(
                    match_text
                )

    # 精确匹配排在模糊匹配前面。
    matches = [
        *exact_matches,
        *partial_matches,
    ]

    matches_truncated = (
        len(matches)
        > MAX_SYMBOL_RESULTS
    )

    matches = matches[
        :MAX_SYMBOL_RESULTS
    ]

    if matches:
        output = [
            f"在 {path} 中找到 "
            f"{len(matches)} 个 "
            "Python 符号：",
            *[
                f"- {match}"
                for match in matches
            ],
        ]
    else:
        output = [
            f"没有在 {path} 中找到 "
            f"Python 符号：{query}"
        ]

    if files_truncated:
        output.extend(
            [
                "",
                f"[文件数量超过 "
                f"{MAX_PYTHON_FILES}，"
                "本次只分析前面的文件]",
            ]
        )

    if matches_truncated:
        output.extend(
            [
                "",
                f"[匹配结果超过 "
                f"{MAX_SYMBOL_RESULTS}，"
                "本次只返回前面的结果]",
            ]
        )

    if parse_warnings:
        output.extend(
            [
                "",
                "以下 Python 文件解析失败：",
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


CODE_STRUCTURE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": (
                "get_python_file_outline"
            ),
            "description": (
                "分析一个 Python 文件的代码结构，"
                "返回 import、类、函数、方法、"
                "嵌套函数及其起止行号。"
                "当用户询问文件结构、文件中"
                "有哪些类或函数、某个类有哪些"
                "方法时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "相对于 workspace 的 "
                            "Python 文件路径，例如 "
                            "demo_project/main.py"
                        ),
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": (
                "search_python_symbol"
            ),
            "description": (
                "在 workspace 中搜索真正的 "
                "Python 类、函数、异步函数和"
                "方法定义。不会把普通字符串、"
                "注释或变量当作符号定义。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "要搜索的符号名称，例如 "
                            "UserService、create_app "
                            "或 create_user"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "相对于 workspace 的"
                            "文件或目录路径，例如 "
                            ". 或 demo_project"
                        ),
                        "default": ".",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


AVAILABLE_CODE_STRUCTURE_TOOLS = {
    "get_python_file_outline": (
        get_python_file_outline
    ),
    "search_python_symbol": (
        search_python_symbol
    ),
}