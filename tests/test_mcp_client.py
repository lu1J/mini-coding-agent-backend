"""Day18 MCP 客户端专项测试。

覆盖：
1. StdioServerParameters：command 必须是 sys.executable（绝不硬编码 python）；
2. args 与 cwd 语义正确（cwd 默认项目根）；
3. 真实 stdio 子进程：list_tools 能发现 project_overview；
4. 真实 stdio 子进程：call_tool 拿到 is_error=False 的真实结果；
5. 服务端不可启动 → 连接期抛异常，绝不静默成功。
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import StdioServerParameters

from app.mcp.client import (
    call_tool,
    list_tools,
    open_mcp_client,
    stdio_server_parameters,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.run(coro)


def test_stdio_parameters_use_sys_executable_and_project_root():
    params = stdio_server_parameters()
    assert isinstance(params, StdioServerParameters)
    assert params.command == sys.executable
    assert params.args == ["-m", "app.mcp.server"]
    assert Path(params.cwd).resolve() == PROJECT_ROOT


def test_stdio_parameters_allow_custom_module_and_cwd(tmp_path):
    params = stdio_server_parameters(
        module="app.mcp.other_server",
        cwd=tmp_path,
        project_root=PROJECT_ROOT,
    )
    assert params.command == sys.executable
    assert params.args == ["-m", "app.mcp.other_server"]
    assert Path(params.cwd).resolve() == Path(tmp_path).resolve()


def test_real_stdio_subprocess_list_tools():
    async def run():
        async with open_mcp_client(stdio_server_parameters()) as client:
            tools = await list_tools(client)
        return [tool.name for tool in tools]

    names = _run(run())
    assert "project_overview" in names


def test_real_stdio_subprocess_call_tool(mcp_project_root):
    async def run():
        params = stdio_server_parameters(project_root=mcp_project_root)
        async with open_mcp_client(params) as client:
            result = await call_tool(client, "project_overview", {})
        return result

    result = _run(run())
    assert result.is_error is False
    text = "".join(
        str(getattr(block, "text", ""))
        for block in (result.content or [])
        if getattr(block, "type", None) == "text"
    )
    data = json.loads(text)
    assert data["project"] == "demo_project"
    assert data["python_files"] >= 1


def test_unreachable_server_raises_connection_error():
    """服务端模块不存在 → 连接期间抛异常，不静默返回空结果。"""
    async def run():
        params = stdio_server_parameters(module="app.mcp.missing_server_xyz")
        async with open_mcp_client(params) as client:
            await list_tools(client)

    with pytest.raises(Exception):
        _run(run())
