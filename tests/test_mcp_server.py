"""Day18 MCP 服务端专项测试。

覆盖：
1. 内存连接下列出工具：只暴露 project_overview 一个只读工具；
2. 内存调用：确定性、结构化概览结果（文件按名称排序、行数自洽）；
3. 安全路径解析：demo_project 缺失 → 明确报错；
4. 安全路径解析：目录逃出 workspace → 拒绝访问；
5. 直接调用底层 project_overview() 的返回结构。
"""

import asyncio
import json
from pathlib import Path

import pytest

import app.mcp.server as server_module
from app.mcp.client import call_tool, list_tools, open_mcp_client
from app.mcp.server import (
    PROJECT_OVERVIEW_TOOL_NAME,
    server,
    project_overview,
)


def _run(coro):
    return asyncio.run(coro)


def _result_text(result) -> str:
    """把 CallToolResult 的 text 内容拼成字符串。"""
    return "".join(
        str(getattr(block, "text", ""))
        for block in (result.content or [])
        if getattr(block, "type", None) == "text"
    )


def test_server_exposes_only_readonly_tool():
    async def inner():
        async with open_mcp_client(server) as client:
            tools = await list_tools(client)

        names = [tool.name for tool in tools]
        assert names == [PROJECT_OVERVIEW_TOOL_NAME]

        tool = tools[0]
        assert tool.description
        # SDK v2 的 Tool.input_schema 是 OpenAI 兼容 JSON Schema。
        assert isinstance(tool.input_schema, dict)
        assert tool.input_schema["type"] == "object"

    _run(inner())


def test_in_memory_call_returns_deterministic_overview():
    async def inner():
        async with open_mcp_client(server) as client:
            first = await call_tool(client, PROJECT_OVERVIEW_TOOL_NAME, {})
            second = await call_tool(client, PROJECT_OVERVIEW_TOOL_NAME, {})

        assert first.is_error is False
        assert second.is_error is False

        first_text = _result_text(first)
        second_text = _result_text(second)
        # 确定性：两次调用内容完全一致。
        assert first_text == second_text

        data = json.loads(first_text)
        assert data["project"] == "demo_project"
        assert data["python_files"] >= 1
        assert data["total_lines"] >= 1

        files = data["files"]
        names = [entry["name"] for entry in files]
        assert names == sorted(names)
        assert data["total_lines"] == sum(entry["lines"] for entry in files)
        for entry in files:
            assert set(entry) == {"name", "lines", "size"}
            assert entry["lines"] >= 1

    _run(inner())


def test_overview_direct_call_shape():
    data = project_overview()
    assert isinstance(data, dict)
    assert sorted(data.keys()) == [
        "files",
        "project",
        "python_files",
        "total_lines",
    ]
    assert data["project"] == "demo_project"


def test_missing_demo_project_raises_clear_error(monkeypatch):
    # DEMO_PROJECT_RELATIVE_PATH 相对 PROJECT_ROOT；放在 workspace 内但不存在。
    monkeypatch.setattr(
        server_module,
        "DEMO_PROJECT_RELATIVE_PATH",
        Path("workspace") / "no_such_demo_dir_xyz",
    )
    with pytest.raises(ValueError, match="demo_project 目录不存在"):
        project_overview()


def test_path_escaping_workspace_is_rejected(monkeypatch):
    # 相对路径解析后逃出 workspace 根目录 → 安全拒绝。
    monkeypatch.setattr(
        server_module,
        "DEMO_PROJECT_RELATIVE_PATH",
        Path("..") / "outside_workspace_evil",
    )
    with pytest.raises(ValueError, match="超出 workspace"):
        project_overview()
