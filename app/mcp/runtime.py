"""Day18 MCP → LangGraph v2 runtime 装配器。

默认 /agent/code/graph/v2 不依赖 MCP（build_fine_grained_graph 的默认注入
仍来自 code_agent.CODE_AGENT_TOOLS / AVAILABLE_CODE_AGENT_TOOLS）。

本装配器只在需要 MCP 扩展的独立 demo runtime 中使用：

    runtime = await assemble_mcp_demo_runtime()
    graph = build_fine_grained_graph(checkpointer=checkpointer, **runtime)

流程（发现与暴露分离）：
1. 真实 stdio 子进程连接服务端（python -m app.mcp.server），
   list_tools() 发现服务端全部工具；
2. allowlist 过滤（当前只允许 project_overview）；
3. 命名冲突检测（MCP 名与内部工具名冲突则拒绝装配）；
4. Schema 转换 → 追加进 tools 列表；
5. build_sync_callable 挂载同步实现 → 追加进 available_tools；
6. 显式登记：TOOL_RISK_LEVELS 登记 low + Executor supporting 注册表登记
   （未登记直接拒绝，不依赖 unknown → low 的宽松路径）。

测试清理：unregister_mcp_demo_tools_from_policy_tables() 撤销第 6 步登记。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent.code_agent import (
    AVAILABLE_CODE_AGENT_TOOLS,
    CODE_AGENT_SYSTEM_PROMPT,
    CODE_AGENT_TOOLS,
)
from app.agent.plan_executor import (
    register_extra_supporting_low_risk_tools,
    unregister_extra_supporting_low_risk_tools,
)
from app.agent.tool_policy import TOOL_RISK_LEVELS
from app.mcp.bridge import (
    MCPToolError,
    MCP_TOOL_RISK_LEVELS,
    build_agent_tool_name,
    build_sync_callable,
    filter_allowlisted,
    mcp_tool_to_agent_schema,
)
from app.mcp.client import (
    McpConnectTarget,
    list_tools,
    open_mcp_client,
    stdio_server_parameters,
)

# Demo 专用提示语：只说明 MCP 概览工具的存在与用法，不改变默认 prompt。
MCP_DEMO_SYSTEM_PROMPT_SUFFIX = (
    "[MCP 扩展]\n"
    "你额外拥有一个 MCP 外部只读工具 mcp_demo_project_overview：\n"
    "调用它可以在不读文件的情况下拿到 demo_project 的整体结构概览\n"
    "（文件清单与行数），适合作为探索的起点。"
    "该工具不修改任何文件；调用没有副作用。"
)


def register_mcp_demo_tools_in_policy_tables() -> None:
    """显式登记 MCP demo 工具：风险 low + Executor supporting（幂等）。"""
    for agent_tool_name, risk_level in MCP_TOOL_RISK_LEVELS.items():
        # 显式覆盖赋值：这是 MCP 模块自己的登记（fail fast 语义），
        # 不依赖 tool_policy 中 unknown → low 的宽松归一化。
        TOOL_RISK_LEVELS[agent_tool_name] = risk_level
    register_extra_supporting_low_risk_tools(*MCP_TOOL_RISK_LEVELS.keys())


def unregister_mcp_demo_tools_from_policy_tables() -> None:
    """撤销登记（测试 teardown / 进程内清理）。"""
    for agent_tool_name, risk_level in MCP_TOOL_RISK_LEVELS.items():
        if TOOL_RISK_LEVELS.get(agent_tool_name) == risk_level:
            del TOOL_RISK_LEVELS[agent_tool_name]
    unregister_extra_supporting_low_risk_tools(*MCP_TOOL_RISK_LEVELS.keys())


async def assemble_mcp_demo_runtime(
    *,
    connect_target: McpConnectTarget | None = None,
) -> dict[str, Any]:
    """装配带 MCP 扩展的 CodeAgent runtime。

    返回 build_fine_grained_graph 可展开的字典：
        {system_prompt, tools, available_tools}

    connect_target 缺省时为真实 stdio 子进程参数
    （sys.executable -m app.mcp.server）。
    """
    target = connect_target or stdio_server_parameters()

    # 1-2. 发现 → allowlist 过滤（真实连接一次以获取工具元数据）。
    async with open_mcp_client(target) as client:
        discovered = await list_tools(client)
    allowlisted = [tool for tool in discovered if filter_allowlisted(tool)]
    if not allowlisted:
        raise MCPToolError(
            "MCP 服务端未暴露任何 allowlist 内的工具"
            f"（allowlist={sorted(MCP_TOOL_RISK_LEVELS)}），拒绝装配。"
        )

    # 3-5. 冲突检测 + Schema 转换 + 同步 callable 挂载。
    extra_schemas: list[dict[str, Any]] = []
    extra_callables: dict[str, Any] = {}
    for tool in allowlisted:
        schema = mcp_tool_to_agent_schema(tool)
        agent_tool_name = str(schema["function"]["name"])
        if agent_tool_name in AVAILABLE_CODE_AGENT_TOOLS:
            raise MCPToolError(
                f"MCP 工具名 {agent_tool_name} 与内部工具冲突，拒绝装配。"
            )
        original_tool_name = str(tool.name)
        extra_schemas.append(schema)
        extra_callables[agent_tool_name] = build_sync_callable(
            original_tool_name=original_tool_name,
            connect_target=target,
            agent_tool_name=agent_tool_name,
        )

    # 6. 显式风险 + supporting 登记（幂等；unregister 可清理）。
    register_mcp_demo_tools_in_policy_tables()

    return {
        "system_prompt": (
            CODE_AGENT_SYSTEM_PROMPT + "\n\n" + MCP_DEMO_SYSTEM_PROMPT_SUFFIX
        ),
        "tools": [*CODE_AGENT_TOOLS, *extra_schemas],
        "available_tools": {
            **AVAILABLE_CODE_AGENT_TOOLS,
            **extra_callables,
        },
    }


def register_defaults_then_sync_assemble() -> dict[str, Any]:
    """同步入口：为没有 asyncio 环境的调用方提供同步装配（脚本用）。"""
    return asyncio.run(assemble_mcp_demo_runtime())
