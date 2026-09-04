"""Day18 MCP → Agent 转换层（bridge）专项测试。

覆盖：
1. 命名空间：mcp_demo_<原工具名> 转换与还原；
2. Schema 转换：形态与内部工具完全一致，input_schema/description 保留；
3. 名称合法性检查：非法字符工具名拒绝转换；
4. allowlist：project_overview 放行、其余工具被过滤；
5. 显式风险登记：mcp_demo_project_overview == low；
6. 未登记工具装配时直接拒绝（不依赖 unknown→low 宽松行为）；
7. is_error=True 结果 → MCPToolExecutionError（mcp_tool_error）；
8. 成功结果：优先 structured_content，其次拼接 TextContent；
9. 同步 wrapper 在纯同步环境可用（内存服务端）；
10. 同步 wrapper 在外部已有 event loop 时安全；
11. 服务端不可用 → MCPConnectionError（mcp_unavailable）；
12. 超时 → MCPTimeoutError（mcp_timeout），不会无限挂起。
"""

import asyncio
import json
import time

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from app.mcp import bridge
from app.mcp.bridge import (
    MCPConnectionError,
    MCP_TOOL_RISK_LEVELS,
    MCPToolError,
    MCPToolExecutionError,
    MCPTimeoutError,
    build_agent_tool_name,
    build_sync_callable,
    filter_allowlisted,
    mcp_tool_to_agent_schema,
    normalize_call_tool_result,
    require_registered_risk,
    resolve_original_tool_name,
    run_async_safely,
)
from app.mcp.client import stdio_server_parameters
from app.mcp.server import server as in_memory_server

ALLOWED_TOOL = Tool(
    name="project_overview",
    description="获取 demo_project 只读概览",
    input_schema={"type": "object", "properties": {}},
)


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ 命名空间


def test_agent_tool_name_namespacing_roundtrip():
    assert build_agent_tool_name("project_overview") == (
        "mcp_demo_project_overview"
    )
    assert resolve_original_tool_name("mcp_demo_project_overview") == (
        "project_overview"
    )


def test_resolve_rejects_non_namespaced_name():
    with pytest.raises(MCPToolError, match="命名空间"):
        resolve_original_tool_name("read_file")


# ------------------------------------------------------------------ Schema 转换


def test_schema_shape_matches_code_agent_tool_format():
    schema = mcp_tool_to_agent_schema(ALLOWED_TOOL)

    # 与 CODE_AGENT_TOOLS 完全相同的形态：
    # {"type": "function", "function": {name, description, parameters}}
    assert schema["type"] == "function"
    function = schema["function"]
    assert function["name"] == "mcp_demo_project_overview"
    assert function["description"] == ALLOWED_TOOL.description
    assert function["parameters"] == ALLOWED_TOOL.input_schema


def test_schema_rejects_illegal_tool_name():
    illegal = Tool(
        name="bad name!",
        description="desc",
        input_schema={"type": "object", "properties": {}},
    )
    with pytest.raises(MCPToolError, match="非法字符"):
        mcp_tool_to_agent_schema(illegal)


# ------------------------------------------------------------------ allowlist / 风险


def test_allowlist_keeps_only_project_overview():
    assert filter_allowlisted(ALLOWED_TOOL) is True
    other = Tool(
        name="edit_file",
        description="desc",
        input_schema={"type": "object", "properties": {}},
    )
    assert filter_allowlisted(other) is False


def test_risk_registration_is_explicit_low():
    assert MCP_TOOL_RISK_LEVELS == {"mcp_demo_project_overview": "low"}
    assert require_registered_risk("mcp_demo_project_overview") == "low"


def test_unregistered_tool_rejected_at_assembly():
    with pytest.raises(MCPToolError, match="风险登记"):
        require_registered_risk("mcp_demo_edit_file")

    # build_sync_callable 装配阶段就拒绝，不等到调用期。
    with pytest.raises(MCPToolError, match="风险登记"):
        build_sync_callable(
            original_tool_name="edit_file",
            connect_target=in_memory_server,
        )


# ------------------------------------------------------------------ 结果处理


def test_is_error_result_raises_execution_error():
    failed = CallToolResult(
        content=[TextContent(text="rm -rf 被拒绝")],
        structured_content=None,
        is_error=True,
    )
    with pytest.raises(MCPToolExecutionError) as excinfo:
        normalize_call_tool_result(failed)
    assert excinfo.value.code == "mcp_tool_error"
    assert "mcp_tool_error" in str(excinfo.value)


def test_structured_content_preferred_over_text():
    result = CallToolResult(
        content=[TextContent(text="fallback text")],
        structured_content={"project": "demo_project", "files": []},
        is_error=False,
    )
    normalized = normalize_call_tool_result(result)
    assert normalized == {"project": "demo_project", "files": []}


def test_text_content_concatenated_when_no_structured():
    result = CallToolResult(
        content=[TextContent(text="第一行"), TextContent(text="第二行")],
        structured_content=None,
        is_error=False,
    )
    assert normalize_call_tool_result(result) == "第一行\n第二行"


# ------------------------------------------------------------------ 同步适配器


def test_sync_wrapper_works_in_plain_sync_env():
    callable_ = build_sync_callable(
        original_tool_name="project_overview",
        connect_target=in_memory_server,
    )
    output = callable_()
    data = json.loads(output)
    assert data["project"] == "demo_project"
    assert data["python_files"] >= 1
    assert callable_.mcp_agent_tool_name == "mcp_demo_project_overview"


def test_sync_wrapper_safe_when_event_loop_already_running():
    """调用线程已经运行 event loop 时也能安全调用（worker 线程自建 loop）。"""
    callable_ = build_sync_callable(
        original_tool_name="project_overview",
        connect_target=in_memory_server,
    )

    async def outer():
        # 直接在当前 loop 线程内做同步调用：不得触碰正在运行的 loop。
        output = callable_()
        return json.loads(output)["project"]

    assert _run(outer()) == "demo_project"


def test_unavailable_server_raises_connection_error():
    bad_params = stdio_server_parameters(module="app.mcp.missing_server_xyz")
    callable_ = build_sync_callable(
        original_tool_name="project_overview",
        connect_target=bad_params,
    )
    with pytest.raises(MCPConnectionError) as excinfo:
        callable_()
    assert excinfo.value.code == "mcp_unavailable"
    assert "mcp_unavailable" in str(excinfo.value)


def test_timeout_does_not_hang():
    async def never_returns():
        await asyncio.sleep(30)

    started = time.monotonic()
    with pytest.raises(MCPTimeoutError) as excinfo:
        run_async_safely(never_returns, timeout_seconds=0.3)
    elapsed = time.monotonic() - started

    assert excinfo.value.code == "mcp_timeout"
    assert "mcp_timeout" in str(excinfo.value)
    # 外层 join 有 5s 兜底余量；超时必须在远小于无限挂起的窗口内返回。
    assert elapsed < 10
