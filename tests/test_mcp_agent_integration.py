"""Day18 MCP × LangGraph v2 Agent 集成专项测试。

覆盖：
1. runtime 装配：发现 → allowlist → Schema 追加 → callable 挂载 → 登记；
2. 冲突检测：MCP 名与内部工具重名 → 拒绝装配；
3. allowlist 拒绝：服务端未暴露白名单工具 → 拒绝装配；
4. 默认 v2 不依赖 MCP：未登记时 mcp 工具被 Executor 拦截（内部工具不受影响）；
5. 注册后全轨迹：真实 stdio 子进程调用 → supporting 放行 → 结果回填；
6. MCP 调用不推进计划步骤（supporting 语义）；
7. MCP 失败 → execute_tool 统一 success=False（ERROR_TYPE_TOOL）→ reflection 恢复；
8. Day17 兼容：role=tool 进入消息链；>4000 chars 结果请求侧压缩、
   State 保存完整原文。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import app.agent.langgraph_v2_nodes as v2_nodes
from app.agent.agent_loop import ERROR_TYPE_TOOL
from app.agent.langgraph_v2_workflow import build_fine_grained_graph
from app.agent.plan_executor import EXECUTOR_ALLOW_SUPPORTING
from app.agent.tool_policy import get_tool_risk_level
from app.mcp import runtime as mcp_runtime
from app.mcp.bridge import (
    MCPToolError,
    build_sync_callable,
)
from app.mcp.client import stdio_server_parameters
from app.mcp.server import server as in_memory_server


# ------------------------------------------------------------------ fakes


class SimpleNamespaceProxy:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def make_fake_tool_call(tool_name, tool_args, call_id="call_v2_001"):
    return SimpleNamespaceProxy(
        id=call_id,
        function=SimpleNamespaceProxy(
            name=tool_name,
            arguments=json.dumps(tool_args, ensure_ascii=False),
        ),
    )


def make_fake_response(message):
    return SimpleNamespaceProxy(choices=[SimpleNamespaceProxy(message=message)])


def make_final_response(content):
    return make_fake_response(
        SimpleNamespaceProxy(content=content, tool_calls=None)
    )


def make_tool_response(tool_calls):
    return make_fake_response(
        SimpleNamespaceProxy(content="", tool_calls=tool_calls)
    )


def make_step(index, title, suggested_tool, risk_level="low", reason="test"):
    return {
        "index": index,
        "title": title,
        "description": f"{title} 测试步骤",
        "suggested_tool": suggested_tool,
        "risk_level": risk_level,
        "reason": reason,
    }


def make_plan(steps):
    return {
        "objective": "测试任务",
        "intents": ["read"],
        "target_paths": ["demo_project/main.py"],
        "suggested_tools": [
            step["suggested_tool"] for step in steps if step.get("suggested_tool")
        ],
        "risk_level": "low",
        "complexity": "simple",
        "needs_approval": False,
        "estimated_steps": len(steps),
        "steps": steps,
        "warnings": [],
        "planner_meta": {"version": "2.1"},
    }


def make_initial_state(user_message="测试任务", max_steps=8, thread_id="v2_mcp"):
    return {
        "thread_id": thread_id,
        "user_message": user_message,
        "max_steps": max_steps,
        "messages": [],
        "steps": [],
        "graph_events": [],
        "pending_tool_calls": [],
        "tool_cursor": 0,
        "model_round": 0,
        "step_counter": 0,
        "reflection_retry_count": 0,
        "premature_final_count": 0,
    }


def make_read_file_tool():
    calls = {"count": 0}

    def read_file(path: str):
        calls["count"] += 1
        return {
            "success": True,
            "result": f"内容：{path}",
            "error": None,
        }

    return read_file, calls


def patch_llm(monkeypatch, responses, captured=None):
    def fake_create(*args, **kwargs):
        if captured is not None:
            captured.append(kwargs["messages"])
        return responses.pop(0)

    monkeypatch.setattr(
        v2_nodes.llm.client.chat.completions,
        "create",
        fake_create,
    )


def build_graph(*, plan, available_tools, tools=None, monkeypatch=None):
    monkeypatch.setattr(v2_nodes, "build_task_plan", lambda _msg: plan)
    return build_fine_grained_graph(
        checkpointer=InMemorySaver(),
        system_prompt="测试系统提示词",
        tools=tools or [],
        available_tools=available_tools,
    )


# ------------------------------------------------------------------ 装配


@pytest.fixture
def mcp_registered():
    mcp_runtime.register_mcp_demo_tools_in_policy_tables()
    yield
    mcp_runtime.unregister_mcp_demo_tools_from_policy_tables()


def test_assemble_mounts_mcp_tool_into_runtime():
    try:
        async def run():
            return await mcp_runtime.assemble_mcp_demo_runtime(
                connect_target=in_memory_server
            )

        runtime = asyncio.run(run())
        mcp_schemas = [
            tool["function"]["name"]
            for tool in runtime["tools"]
            if str(tool.get("type")) == "function"
            and str(tool["function"]["name"]).startswith("mcp_demo_")
        ]
        assert mcp_schemas == ["mcp_demo_project_overview"]
        assert "mcp_demo_project_overview" in runtime["available_tools"]
        assert "mcp_demo_project_overview" in runtime["system_prompt"]

        assert get_tool_risk_level("mcp_demo_project_overview") == "low"
    finally:
        # 无论断言成败都清理登记，避免污染同进程后续测试。
        mcp_runtime.unregister_mcp_demo_tools_from_policy_tables()
    assert get_tool_risk_level("mcp_demo_project_overview") == "unknown"


def test_assemble_rejects_name_conflict_with_internal_tool(monkeypatch):
    monkeypatch.setattr(
        mcp_runtime,
        "AVAILABLE_CODE_AGENT_TOOLS",
        {
            **mcp_runtime.AVAILABLE_CODE_AGENT_TOOLS,
            "mcp_demo_project_overview": (lambda: None),
        },
    )

    async def run():
        return await mcp_runtime.assemble_mcp_demo_runtime(
            connect_target=in_memory_server
        )

    with pytest.raises(MCPToolError, match="冲突"):
        asyncio.run(run())


def test_assemble_rejects_when_no_allowlisted_tool_exposed():
    from mcp.server import MCPServer

    evil_server = MCPServer(name="evil-mcp", description="只有危险工具")

    @evil_server.tool(name="delete_everything", description="删除一切")
    def _evil() -> dict:
        return {"ok": True}

    async def run():
        return await mcp_runtime.assemble_mcp_demo_runtime(
            connect_target=evil_server
        )

    with pytest.raises(MCPToolError, match="allowlist"):
        asyncio.run(run())


# ------------------------------------------------------------------ 默认隔离


def test_default_v2_blocks_mcp_tool_without_registration(monkeypatch):
    """未登记（默认 /agent/code/graph/v2 配置）时，MCP 工具不会被执行。"""
    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, read_calls = make_read_file_tool()
    mcp_calls = {"count": 0}

    def fake_mcp_tool():
        mcp_calls["count"] += 1
        return "MCP 已执行"

    graph = build_graph(
        monkeypatch=monkeypatch,
        plan=plan,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "mcp_demo_project_overview",
                    "description": "MCP 概览",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        available_tools={
            "read_file": read_tool,
            "mcp_demo_project_overview": fake_mcp_tool,
        },
    )
    patch_llm(
        monkeypatch,
        [
            make_tool_response(
                [make_fake_tool_call("mcp_demo_project_overview", {}, "call_mcp")]
            ),
            make_tool_response(
                [
                    make_fake_tool_call(
                        "read_file", {"path": "demo_project/main.py"}, "call_read"
                    )
                ]
            ),
            make_final_response("读取完成。"),
        ],
    )

    result = graph.invoke(
        make_initial_state(), config={"configurable": {"thread_id": "v2_no_mcp"}}
    )

    assert mcp_calls["count"] == 0
    assert read_calls["count"] == 1
    types = [step["type"] for step in result["steps"]]
    assert "executor_blocked" in types
    blocked = next(
        step for step in result["steps"] if step["type"] == "executor_blocked"
    )
    assert blocked["tool_name"] == "mcp_demo_project_overview"
    assert blocked["error"]["type"] == "plan_step_violation"
    assert result["final_result"]["status"] == "finished"


# ------------------------------------------------------------------ 注册后全轨迹


def test_registered_mcp_supporting_call_full_trace(
    monkeypatch, mcp_registered
):
    """真实 stdio 子进程：mcp_demo_project_overview 以 supporting 身份放行。"""
    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, read_calls = make_read_file_tool()
    mcp_callable = build_sync_callable(
        original_tool_name="project_overview",
        connect_target=stdio_server_parameters(),
    )
    graph = build_graph(
        monkeypatch=monkeypatch,
        plan=plan,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "mcp_demo_project_overview",
                    "description": "MCP 概览",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        available_tools={
            "read_file": read_tool,
            "mcp_demo_project_overview": mcp_callable,
        },
    )
    captured = []
    patch_llm(
        monkeypatch,
        [
            make_tool_response(
                [make_fake_tool_call("mcp_demo_project_overview", {}, "call_mcp")]
            ),
            make_tool_response(
                [
                    make_fake_tool_call(
                        "read_file", {"path": "demo_project/main.py"}, "call_read"
                    )
                ]
            ),
            make_final_response("读取完成。"),
        ],
        captured=captured,
    )

    result = graph.invoke(
        make_initial_state(), config={"configurable": {"thread_id": "v2_mcp_ok"}}
    )

    # MCP 工具真实执行并成功。
    tool_calls = [
        step
        for step in result["steps"]
        if step["type"] == "tool_call" and step["tool_name"]
    ]
    mcp_record = next(
        step for step in tool_calls if step["tool_name"] == "mcp_demo_project_overview"
    )
    assert mcp_record["success"] is True
    assert mcp_record["executor_decision"]["decision"] == EXECUTOR_ALLOW_SUPPORTING
    assert mcp_record["executor_decision"]["completes_current_step"] is False
    assert "total_lines" in str(mcp_record["tool_result"])
    assert mcp_record["error"] is None
    assert read_calls["count"] == 1

    # supporting 调用不推进计划步骤；随后 read_file 完成唯一步骤。
    executor_state = result["executor_state"]
    assert executor_state["supporting_calls"] == 1
    assert executor_state["completed_steps"] == 1
    assert executor_state["status"] == "completed"

    # Day17：MCP 结果作为 role=tool 进入 State 消息链（原文保存）。
    tool_messages = [
        message
        for message in result["messages"]
        if message.get("role") == "tool"
    ]
    mcp_tool_message = next(
        message for message in tool_messages if message.get("tool_call_id") == "call_mcp"
    )
    assert "total_lines" in str(mcp_tool_message["content"])

    # Day17：第二轮模型请求（含 MCP 结果）由 Context Builder 拼装。
    second_request = captured[1]
    request_tool_message = next(
        message
        for message in second_request
        if message.get("role") == "tool"
        and message.get("tool_call_id") == "call_mcp"
    )
    assert "total_lines" in str(request_tool_message["content"])
    context_events = [
        event
        for event in result["graph_events"]
        if event["event"] == "context_built"
    ]
    assert context_events
    assert all(event.get("context_meta") for event in context_events)


def test_mcp_call_does_not_advance_plan_step(monkeypatch, mcp_registered):
    """supporting 语义：MCP 调用后 current_step_position 不变。"""
    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, _ = make_read_file_tool()
    mcp_callable = build_sync_callable(
        original_tool_name="project_overview",
        connect_target=in_memory_server,
    )
    graph = build_graph(
        monkeypatch=monkeypatch,
        plan=plan,
        available_tools={
            "read_file": read_tool,
            "mcp_demo_project_overview": mcp_callable,
        },
    )
    patch_llm(
        monkeypatch,
        [
            make_tool_response(
                [make_fake_tool_call("mcp_demo_project_overview", {}, "call_mcp")]
            ),
            make_tool_response(
                [
                    make_fake_tool_call(
                        "read_file", {"path": "demo_project/main.py"}, "call_read"
                    )
                ]
            ),
            make_final_response("读取完成。"),
        ],
    )

    result = graph.invoke(
        make_initial_state(), config={"configurable": {"thread_id": "v2_mcp_step"}}
    )

    assert result["executor_state"]["current_step_position"] == 1
    assert result["executor_state"]["completed_steps"] == 1
    assert result["final_result"]["status"] == "finished"


def test_mcp_failure_becomes_tool_error_and_recovers(monkeypatch, mcp_registered):
    """MCP 服务端不可用 → bridge 抛 MCPConnectionError →
    既有 execute_tool 捕获 → success=False（ERROR_TYPE_TOOL）→ reflection 恢复。"""
    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, read_calls = make_read_file_tool()
    bad_params = stdio_server_parameters(module="app.mcp.missing_server_xyz")
    mcp_callable = build_sync_callable(
        original_tool_name="project_overview",
        connect_target=bad_params,
    )
    graph = build_graph(
        monkeypatch=monkeypatch,
        plan=plan,
        available_tools={
            "read_file": read_tool,
            "mcp_demo_project_overview": mcp_callable,
        },
    )
    patch_llm(
        monkeypatch,
        [
            make_tool_response(
                [make_fake_tool_call("mcp_demo_project_overview", {}, "call_mcp")]
            ),
            make_tool_response(
                [
                    make_fake_tool_call(
                        "read_file", {"path": "demo_project/main.py"}, "call_read"
                    )
                ]
            ),
            make_final_response("已用内部工具完成读取。"),
        ],
    )

    result = graph.invoke(
        make_initial_state(), config={"configurable": {"thread_id": "v2_mcp_fail"}}
    )

    tool_calls = [
        step
        for step in result["steps"]
        if step["type"] == "tool_call" and step["tool_name"]
    ]
    mcp_record = next(
        step for step in tool_calls if step["tool_name"] == "mcp_demo_project_overview"
    )
    assert mcp_record["success"] is False
    assert mcp_record["error"]["type"] == ERROR_TYPE_TOOL
    assert "mcp_unavailable" in str(mcp_record["tool_result"])

    # 模型读到失败反馈后改用内部工具，任务正常收尾。
    assert read_calls["count"] == 1
    assert result["final_result"]["status"] == "finished"
    assert result["executor_state"]["status"] == "completed"


# ------------------------------------------------------------------ Day17 压缩兼容


def test_day17_long_mcp_result_compressed_in_request_not_in_state(
    monkeypatch, mcp_registered
):
    """>4000 chars 的 MCP 结果：请求侧压缩、State 保存完整原文。"""
    from mcp.server import MCPServer

    plan = make_plan([make_step(0, "读取文件", "read_file")])
    read_tool, _ = make_read_file_tool()

    # 内存 MCP 服务端返回超长（>4000 chars）结果。
    big_server = MCPServer(name="big-mcp", description="返回长内容")

    @big_server.tool(name="project_overview", description="项目概览")
    def _big_overview() -> dict:
        return {"project": "demo_project", "padding": "x" * 5000}

    mcp_callable = build_sync_callable(
        original_tool_name="project_overview",
        connect_target=big_server,
    )
    graph = build_graph(
        monkeypatch=monkeypatch,
        plan=plan,
        available_tools={
            "read_file": read_tool,
            "mcp_demo_project_overview": mcp_callable,
        },
    )
    captured = []
    patch_llm(
        monkeypatch,
        [
            make_tool_response(
                [make_fake_tool_call("mcp_demo_project_overview", {}, "call_mcp")]
            ),
            make_tool_response(
                [
                    make_fake_tool_call(
                        "read_file", {"path": "demo_project/main.py"}, "call_read"
                    )
                ]
            ),
            make_final_response("读取完成。"),
        ],
        captured=captured,
    )

    result = graph.invoke(
        make_initial_state(), config={"configurable": {"thread_id": "v2_mcp_long"}}
    )

    tool_message = next(
        message
        for message in result["messages"]
        if message.get("role") == "tool"
        and message.get("tool_call_id") == "call_mcp"
    )
    raw_content = str(tool_message["content"])

    # State 保存完整真实结果（>4000 chars，未压缩）。
    assert len(raw_content) > 4000
    assert "x" * 5000 in raw_content

    # 请求侧（第二轮 LLM 请求）压缩到 <4000 chars。
    second_request = captured[1]
    request_tool_message = next(
        message
        for message in second_request
        if message.get("role") == "tool" and message.get("tool_call_id") == "call_mcp"
    )
    assert len(str(request_tool_message["content"])) < 4000

    context_event = next(
        event
        for event in result["graph_events"]
        if event["event"] == "context_built" and event.get("model_round") == 2
    )
    meta = context_event["context_meta"]
    compressed_records = meta.get("compressed_messages") or []
    assert any(
        record.get("role") == "tool" and record.get("original_chars", 0) > 4000
        for record in compressed_records
    )
    assert result["final_result"]["status"] == "finished"
