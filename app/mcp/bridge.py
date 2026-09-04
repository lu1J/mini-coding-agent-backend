"""Day18 MCP → Agent 工具转换层（bridge）。

职责：
1. 命名空间：MCP 原工具名 → Agent 内唯一名
       project_overview → mcp_demo_project_overview
   避免与内部工具重名，也让风险登记/策略表可按前缀识别；
2. allowlist（发现与暴露分离）：客户端可以 list_tools 发现服务端全部工具，
   但只有 allowlist 内的工具才能被转换并装配进 Agent runtime；
3. 风险登记：Agent 内真正暴露的 MCP 工具必须显式登记风险等级
   （本版只允许 low），未登记 → 装配阶段直接拒绝，
   不依赖工具策略层 unknown → low 的既有宽松行为；
4. Schema 转换：MCP Tool（input_schema）→ 本项目 OpenAI Function Calling
   Schema（形态与 CODE_AGENT_TOOLS 完全一致）；
5. 同步调用适配器（sync-to-async bridge）：现有 Agent execute_tool 使用
   同步 callable（tool_func(**args)），而 MCP v2 Client 是异步对象。
   适配器每次调用在独立 worker 线程内自建 event loop 执行异步 MCP 调用，
   主线程带明确超时等待；不依赖调用线程是否已有 event loop，
   不使用 nest_asyncio，不修改全局 event loop。

错误处理约定（不要重写 agent_loop.execute_tool）：
- MCP 工具执行失败 / 连接失败 / 超时 → bridge 抛出明确异常（带分类代码），
  由现有 execute_tool 的 except Exception 统一捕获并产生 success=False；
- 成功路径只返回普通、JSON 可序列化的内容数据，
  不自行包 {"success": True, ...}（外层会统一包装）。

性能限制（第一版明确接受）：
- 每次 MCP 工具调用都会新建 worker 线程 + 新建 stdio 子进程/内存连接，
  无连接池；适合演示与低频只读调用，不适合高频生产调用。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from typing import Any, Awaitable, Callable

from mcp import Client, StdioServerParameters

from app.agent.tool_policy import TOOL_RISK_LOW
from app.mcp.client import open_mcp_client

# ------------------------------------------------------------------ 常量

# Agent 内命名空间前缀：mcp_demo_<原工具名>。
MCP_TOOL_NAMESPACE = "mcp_demo_"

# allowlist：允许从 MCP 服务端装配进 Agent 的工具（MCP 原工具名）。
# 未列入的工具可以被客户端发现，但绝不能转换并暴露给模型。
MCP_ALLOWLIST = frozenset({"project_overview"})

# 显式风险登记：Agent 内暴露的工具名 → 风险等级。
# 未登记的工具在装配阶段被拒绝（fail fast），不依赖 unknown→low 的宽松路径。
MCP_TOOL_RISK_LEVELS = {
    f"{MCP_TOOL_NAMESPACE}{MCP_ALLOWLIST_ITEM}": TOOL_RISK_LOW
    for MCP_ALLOWLIST_ITEM in MCP_ALLOWLIST
}

# 默认单次 MCP 调用超时（秒），与项目 run_command 的 15 秒先例对齐。
DEFAULT_MCP_CALL_TIMEOUT_SECONDS = 15.0

# 线程同步等待的额外兜底余量：worker 内部先按超时终止，
# 主线程 join 再多等余量，防止异常情况下无限挂起。
_WORKER_JOIN_GRACE_SECONDS = 5.0

# OpenAI Function Calling 工具名的合法字符。
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


# ------------------------------------------------------------------ 错误类型

class MCPToolError(Exception):
    """MCP 相关错误基类，携带分类代码。

    分类代码（供错误消息与审计使用）：
    - mcp_tool_error：工具执行失败（含 result.is_error == True）；
    - mcp_unavailable：服务端不可用 / 连接失败；
    - mcp_timeout：调用超时。
    """

    code = "mcp_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class MCPToolExecutionError(MCPToolError):
    code = "mcp_tool_error"


class MCPConnectionError(MCPToolError):
    code = "mcp_unavailable"


class MCPTimeoutError(MCPToolError):
    code = "mcp_timeout"


# ------------------------------------------------------------------ 命名空间

def build_agent_tool_name(original_tool_name: str) -> str:
    """MCP 原工具名 → Agent 内唯一名：mcp_demo_<原工具名>。"""
    return f"{MCP_TOOL_NAMESPACE}{original_tool_name}"


def resolve_original_tool_name(agent_tool_name: str) -> str:
    """Agent 内名 → MCP 原工具名（去掉命名空间前缀）。"""
    if not agent_tool_name.startswith(MCP_TOOL_NAMESPACE):
        raise MCPToolError(
            f"工具名 {agent_tool_name} 不属于 MCP 命名空间 {MCP_TOOL_NAMESPACE}。"
        )
    return agent_tool_name[len(MCP_TOOL_NAMESPACE):]


# ------------------------------------------------------------------ Schema 转换

def mcp_tool_to_agent_schema(tool: Any) -> dict[str, Any]:
    """把 MCP Tool 对象转换成当前项目的 OpenAI Function Calling Schema。

    转换后形态必须与 CODE_AGENT_TOOLS 一致：
        {"type": "function", "function": {name, description, parameters}}
    """
    original_name = str(tool.name)
    if not _TOOL_NAME_PATTERN.match(original_name):
        raise MCPToolError(
            f"MCP 工具名 {original_name!r} 含非法字符，拒绝转换。"
        )

    agent_name = build_agent_tool_name(original_name)
    description = str(getattr(tool, "description", None) or "")
    if not description:
        description = f"MCP 外部工具：{original_name}（只读演示）。"

    # input_schema 已经是 OpenAI 兼容的 JSON Schema 对象（dict），原样保留。
    input_schema = getattr(tool, "input_schema", None) or {
        "type": "object",
        "properties": {},
    }

    return {
        "type": "function",
        "function": {
            "name": agent_name,
            "description": description,
            "parameters": input_schema,
        },
    }


# ------------------------------------------------------------------ allowlist / 风险

def filter_allowlisted(tool: Any) -> bool:
    """allowlist 过滤器：只有列入白名单的 MCP 工具才能装配。"""
    return getattr(tool, "name", None) in MCP_ALLOWLIST


def require_registered_risk(agent_tool_name: str) -> str:
    """装配阶段风险检查：未显式登记的工具直接拒绝。"""
    risk = MCP_TOOL_RISK_LEVELS.get(agent_tool_name)
    if risk is None:
        raise MCPToolError(
            f"MCP 工具 {agent_tool_name} 没有显式风险登记，"
            "拒绝装配进 Agent（不允许依赖 unknown→low 的宽松行为）。"
        )
    return risk


# ------------------------------------------------------------------ 结果处理

def normalize_call_tool_result(result: Any) -> Any:
    """把 MCP CallToolResult 规范化为普通 JSON 可序列化数据。

    - result.is_error == True：视为工具失败，抛出 MCPToolExecutionError；
    - 成功时优先使用 structured_content（结构数据）；
      没有则拼接全部 TextContent 文本；
      都没有则回退为字符串表示。
    """
    if getattr(result, "is_error", False):
        raise MCPToolExecutionError(
            f"mcp_tool_error: MCP 工具返回 is_error=True。"
            f"内容：{_describe_result(result)}"
        )

    structured = getattr(result, "structured_content", None)
    if structured is not None:
        # structured_content 可能是任意 JSON 兼容数据；保持原样返回，
        # execute_tool 外层会 str() 统一成文本。
        return structured

    content = getattr(result, "content", None) or []
    text_parts: list[str] = []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(str(getattr(block, "text", "")))
    if text_parts:
        return "\n".join(text_parts)

    return str(result)


def _describe_result(result: Any) -> str:
    try:
        return json.dumps(
            {
                "is_error": getattr(result, "is_error", None),
                "content": [
                    getattr(block, "text", str(block))
                    for block in (getattr(result, "content", None) or [])
                ][:3],
            },
            ensure_ascii=False,
        )
    except Exception:  # noqa: BLE001 - 兜底描述
        return str(result)


# ------------------------------------------------------------------ 同步适配器

def run_async_safely(
    async_fn: Callable[[], Awaitable[Any]],
    *,
    timeout_seconds: float = DEFAULT_MCP_CALL_TIMEOUT_SECONDS,
) -> Any:
    """sync-to-async bridge：在独立 worker 线程内执行异步调用。

    安全属性：
    - 普通同步环境（无 event loop）可以调用；
    - 调用线程已经存在运行中的 asyncio event loop 时也可以调用
      （worker 线程新建自己的 loop，不触碰调用线程的 loop）；
    - 内部用 asyncio.wait_for 实现明确超时，worker 必然在超时后退出；
    - 主线程 join 带兜底余量，不允许无限挂住；
    - 不使用 nest_asyncio，不修改全局 event loop；
    - worker 为 daemon 线程，进程退出不被阻塞。
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0。")

    result_box: dict[str, Any] = {}

    def _worker() -> None:
        async def _with_timeout() -> Any:
            return await asyncio.wait_for(async_fn(), timeout=timeout_seconds)

        try:
            result_box["value"] = asyncio.run(_with_timeout())
        except BaseException as error_value:  # noqa: BLE001 - 全部带回主线程
            result_box["error"] = error_value

    worker = threading.Thread(target=_worker, name="mcp-sync-bridge", daemon=True)
    worker.start()
    worker.join(timeout_seconds + _WORKER_JOIN_GRACE_SECONDS)

    if worker.is_alive():
        # 理论上 wait_for 保证 worker 会退出；此处为兜底防御。
        raise MCPTimeoutError(
            f"mcp_timeout: MCP 调用 worker 线程未在 "
            f"{timeout_seconds + _WORKER_JOIN_GRACE_SECONDS:.1f}s 内退出，"
            "已放弃等待（daemon 线程不再阻塞进程）。"
        )

    if "error" in result_box:
        error_value = result_box["error"]
        if isinstance(error_value, MCPToolError):
            raise error_value
        if isinstance(error_value, asyncio.TimeoutError):
            raise MCPTimeoutError(
                f"mcp_timeout: MCP 调用超过 {timeout_seconds}s 未返回。"
            ) from error_value
        raise MCPToolExecutionError(
            "mcp_tool_error: MCP 调用执行失败。"
            f"detail: {error_value}"
        ) from error_value

    return result_box["value"]


# ------------------------------------------------------------------ 同步包装函数

def build_sync_callable(
    *,
    original_tool_name: str,
    connect_target: Any,
    timeout_seconds: float = DEFAULT_MCP_CALL_TIMEOUT_SECONDS,
    agent_tool_name: str | None = None,
) -> Callable[..., Any]:
    """生成可放入 available_tools 的同步 callable。

    形态与内部工具一致：tool_func(**tool_args) → 内容数据。
    每次调用建立一次短连接（性能限制见模块 docstring）。

    connect_target：MCPServer 实例（内存）或 StdioServerParameters（子进程）。
    """
    resolved_agent_name = agent_tool_name or build_agent_tool_name(
        original_tool_name
    )
    # 装配前再次确认该工具已显式登记风险（fail fast）。
    require_registered_risk(resolved_agent_name)

    async def _invoke(**tool_args: Any) -> Any:
        try:
            async with open_mcp_client(connect_target) as client:
                result = await client.call_tool(original_tool_name, tool_args)
        except MCPToolError:
            raise
        except Exception as error_value:  # noqa: BLE001 - 连接/调用失败统一分类
            raise MCPConnectionError(
                "mcp_unavailable: MCP 服务端不可用或调用失败。"
                f"detail: {error_value}"
            ) from error_value
        return normalize_call_tool_result(result)

    def sync_callable(**tool_args: Any) -> Any:
        return run_async_safely(
            lambda: _invoke(**tool_args),
            timeout_seconds=timeout_seconds,
        )

    # 保留元信息，便于测试与审计。
    sync_callable.mcp_original_tool_name = original_tool_name  # type: ignore[attr-defined]
    sync_callable.mcp_agent_tool_name = resolved_agent_name  # type: ignore[attr-defined]
    return sync_callable
