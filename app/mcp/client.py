"""Day18 MCP 客户端连接工厂。

基于当前官方 MCP Python SDK v2 的 Client 对象做薄封装，提供两种连接：

1. 内存直接连接（测试 / 确定性验证）：
       client = await open_mcp_client(server)   # server 是 MCPServer 实例

2. 真实 stdio 子进程连接（Agent 进程 → 子进程）：
       params = stdio_server_parameters()
       client = await open_mcp_client(params)

统一暴露 list_tools / call_tool 两个能力入口。
不要硬编码 python：子进程命令必须用 sys.executable，保证走当前虚拟环境。
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Union

from mcp import Client, StdioServerParameters

# 可连接目标：MCPServer 实例（内存）或 StdioServerParameters（真实子进程）。
McpConnectTarget = Union[Any, StdioServerParameters]

DEFAULT_MCP_SERVER_MODULE = "app.mcp.server"


def stdio_server_parameters(
    *,
    module: str = DEFAULT_MCP_SERVER_MODULE,
    project_root: Path | str | None = None,
    cwd: Path | str | None = None,
) -> StdioServerParameters:
    """构造启动本项目 MCP 服务端的 stdio 参数。

    - command 固定为 sys.executable（当前虚拟环境解释器）；
    - args 为 ["-m", module]；
    - cwd 默认取项目根（project_root 缺省时按本文件位置推导），
      保证服务端内相对 workspace 的路径解析一致。
    """
    if project_root is None:
        # app/mcp/client.py → parents[0]=mcp, [1]=app, [2]=项目根。
        project_root = Path(__file__).resolve().parents[2]
    resolved_root = Path(project_root).resolve()
    resolved_cwd = Path(cwd).resolve() if cwd is not None else resolved_root
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        cwd=str(resolved_cwd),
    )


@asynccontextmanager
async def open_mcp_client(target: McpConnectTarget) -> AsyncIterator[Client]:
    """打开一个已连接的 MCP Client。

    用法：
        async with open_mcp_client(params) as client:
            tools = await list_tools(client)
            result = await call_tool(client, "project_overview", {})
    """
    async with Client(target) as client:
        yield client


async def list_tools(client: Client) -> list[Any]:
    """获取 MCP 服务端声明的工具列表（保持 SDK Tool 对象原样）。"""
    listed = await client.list_tools()
    return list(listed.tools)


async def call_tool(
    client: Client,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> Any:
    """调用 MCP 工具，返回 SDK 的 CallToolResult 原对象。"""
    return await client.call_tool(tool_name, arguments)
