"""Day18 本地只读 MCP 服务端。

只暴露一个低风险只读工具：

    project_overview()

固定扫描 workspace/demo_project（与 CodeAgent 工作区语义一致），
不接受任意文件系统路径；只读、不修改任何文件、结果确定。

通过 stdio 启动：

    python -m app.mcp.server
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp.server import MCPServer

PROJECT_OVERVIEW_TOOL_NAME = "project_overview"

# app/mcp/server.py → parents[0]=mcp, [1]=app, [2]=项目根。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_PROJECT_RELATIVE_PATH = Path("workspace") / "demo_project"


def _resolve_demo_project_dir() -> Path:
    """安全解析 demo_project 目录：必须位于项目 workspace 内。"""
    project_dir = (PROJECT_ROOT / DEMO_PROJECT_RELATIVE_PATH).resolve()
    workspace_root = (PROJECT_ROOT / "workspace").resolve()
    try:
        project_dir.relative_to(workspace_root)
    except ValueError as error_value:
        raise ValueError(
            "demo_project 路径超出 workspace 安全范围，拒绝访问。"
        ) from error_value
    if not project_dir.is_dir():
        raise ValueError(
            "demo_project 目录不存在："
            f"{DEMO_PROJECT_RELATIVE_PATH}。"
            "请确认 workspace 中存在该演示项目。"
        )
    return project_dir


def project_overview() -> dict:
    """生成 demo_project 的确定性只读概览。

    返回结构化信息：
    - project：项目名；
    - python_files：Python 文件数量；
    - total_lines：Python 文件总行数；
    - files：按名称排序的文件明细。
    """
    project_dir = _resolve_demo_project_dir()

    files: list[dict] = []
    total_lines = 0
    for path in sorted(project_dir.glob("*.py")):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        total_lines += lines
        files.append(
            {
                "name": path.name,
                "lines": lines,
                "size": path.stat().st_size,
            }
        )

    return {
        "project": "demo_project",
        "python_files": len(files),
        "total_lines": total_lines,
        "files": files,
    }


server = MCPServer(
    name="mini-agent-demo",
    description=(
        "Mini Coding Agent 本地只读演示服务端："
        "提供 demo_project 项目概览。"
    ),
)


@server.tool(
    name=PROJECT_OVERVIEW_TOOL_NAME,
    description=(
        "获取 workspace/demo_project 演示项目的只读概览："
        "Python 文件数量、总行数与文件明细。"
        "低风险辅助工具，不修改任何文件。"
    ),
)
def _project_overview_tool() -> dict:
    return project_overview()


def main() -> None:
    """stdio 启动入口：python -m app.mcp.server。"""
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
