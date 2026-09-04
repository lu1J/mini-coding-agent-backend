"""Day18 MCP 外部工具接入：真实 DeepSeek E2E 演示脚本。

任务（只读，不修改任何文件）：

    先通过 MCP 项目概览工具了解 demo_project，
    再读取其中的 main.py，最后总结这个演示项目的结构。

链路：真实 DeepSeek 模型 → LangGraph v2 runtime
      → mcp_demo_project_overview（Agent 内名）
      → bridge 同步适配器 → worker 线程内异步 MCP Client
      → stdio 子进程（python -m app.mcp.server）→ project_overview()

运行（项目根目录）：

    python scripts/mcp_demo.py

需要 .env 提供 DEEPSEEK_API_KEY。输出包含 Planner 计划、
工具调用轨迹与最终回答。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)

# python scripts/mcp_demo.py 直接运行时 sys.path[0] 是 scripts/，
# 需要显式把项目根加入模块搜索路径才能 import app.*。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 终端

from app.agent.langgraph_v2_service import (  # noqa: E402
    DEFAULT_V2_GRAPH_DB,
    _config,
    _initial_state,
    new_v2_thread_id,
    open_v2_graph_checkpointer,
)
from app.agent.langgraph_v2_workflow import build_fine_grained_graph  # noqa: E402
from app.mcp.runtime import assemble_mcp_demo_runtime  # noqa: E402

DEMO_TASK = (
    "先通过 MCP 项目概览工具了解 demo_project，"
    "再读取其中的 main.py，最后总结这个演示项目的结构。"
    "只读，不修改任何文件。"
)

# 计划步骤建议工具不属于 MCP 时，模型第一步大概率先调 MCP 概览
# （supporting 放行、不推进步骤），随后按计划读文件。
PLAN_IGNORE_TYPES = {"plan_created", "context_built", "model_call"}


def _truncate(text: Any, limit: int = 160) -> str:
    flat = str(text or "").replace("\n", " ")
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _print_plan(task_plan: Any) -> None:
    print("=" * 72)
    print("[Planner] 生成的任务计划")
    if not isinstance(task_plan, dict):
        print("（无计划）")
        return
    print(f"objective: {_truncate(task_plan.get('objective'), 120)}")
    for step in task_plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        print(
            f"  step {step.get('index')}: {step.get('title')}"
            f"  [{step.get('suggested_tool')}]"
            f"  risk={step.get('risk_level')}"
        )


def _print_trace(steps: list[dict[str, Any]]) -> None:
    print("=" * 72)
    print("[工具调用轨迹]")
    if not steps:
        print("（无步骤记录）")
        return
    for record in steps:
        if not isinstance(record, dict):
            continue
        step_type = str(record.get("type") or "")
        if step_type in PLAN_IGNORE_TYPES:
            continue
        tool_name = record.get("tool_name")
        success = record.get("success")
        plan_index = record.get("plan_step_index")
        if step_type == "tool_call" and tool_name:
            print(
                f"  #{record.get('step')} round={record.get('model_round')}"
                f" plan_step={plan_index} tool={tool_name}"
                f" args={_truncate(record.get('tool_args'), 100)}"
            )
            print(
                f"      success={success} result={_truncate(record.get('tool_result'), 200)}"
            )
        elif step_type in {"executor_blocked", "policy_blocked"}:
            print(
                f"  #{record.get('step')} {step_type}: tool={tool_name}"
                f" result={_truncate(record.get('tool_result'), 200)}"
            )
        elif step_type == "final_answer":
            print(f"  #{record.get('step')} final_answer success={success}")
        else:
            print(f"  #{record.get('step')} {step_type} {tool_name or ''}")


def _print_summary(raw_result: dict[str, Any]) -> None:
    print("=" * 72)
    print("[最终回答]")
    final_result = raw_result.get("final_result")
    if isinstance(final_result, dict):
        answer = final_result.get("answer") or raw_result.get("answer") or ""
    else:
        answer = raw_result.get("answer") or ""
    print(str(answer))
    status = raw_result.get("status")
    error = raw_result.get("error")
    if error:
        print(f"[状态] {status} error={error}")
    else:
        print(f"[状态] {status}")


def main() -> None:
    print(f"任务：{DEMO_TASK}")

    runtime = asyncio.run(assemble_mcp_demo_runtime())
    mcp_schemas = [
        schema["function"]["name"]
        for schema in runtime["tools"]
        if str(schema.get("type")) == "function"
        and str(schema["function"]["name"]).startswith("mcp_demo_")
    ]
    print(f"[装配] 模型可见工具 {len(runtime['tools'])} 个，"
          f"其中 MCP 工具：{mcp_schemas}")

    thread_id = new_v2_thread_id()
    with open_v2_graph_checkpointer(DEFAULT_V2_GRAPH_DB) as checkpointer:
        graph = build_fine_grained_graph(checkpointer=checkpointer, **runtime)
        raw_result = graph.invoke(
            _initial_state(
                thread_id=thread_id,
                user_message=DEMO_TASK,
                max_steps=8,
            ),
            config=_config(thread_id),
        )

    _print_plan(raw_result.get("task_plan"))
    _print_trace(raw_result.get("steps") or [])
    _print_summary(raw_result)
    print("=" * 72)
    print(f"thread_id={thread_id}")


if __name__ == "__main__":
    main()
