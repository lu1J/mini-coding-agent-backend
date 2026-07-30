import json
import time
from datetime import datetime
from typing import Any

from app.agent.agent_loop import build_clean_tool_calls, execute_tool
from app.agent.approval_store import save_pending_action
from app.agent.run_logger import save_agent_run
from app.agent.status import (
    AGENT_STATUS_FAILED,
    AGENT_STATUS_FINISHED,
    AGENT_STATUS_MAX_STEPS_REACHED,
    AGENT_STATUS_WAITING_APPROVAL,
    ERROR_TYPE_MODEL,
    ERROR_TYPE_TOOL,
)
from app.agent.tool_policy import get_tool_risk_level, tool_requires_approval
from app.llm.deepseek_client import llm
from app.agent.code_agent import (
    CODE_AGENT_SYSTEM_PROMPT,
    CODE_AGENT_TOOLS,
    AVAILABLE_CODE_AGENT_TOOLS,
)


CODE_AGENT_STREAM_SYSTEM_PROMPT = (
    CODE_AGENT_SYSTEM_PROMPT
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def duration_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)


def sse_event(event: str, data: dict[str, Any]) -> str:
    """
    构造 SSE 事件。

    这里统一把 event 放进 JSON 里，前端只需要解析 data 即可。
    """
    payload = {
        "event": event,
        **data,
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def save_stream_log(
    *,
    status: str,
    user_message: str,
    answer: str,
    max_steps: int,
    steps: list[dict[str, Any]],
    error: dict[str, Any] | None = None,
    pending_action: dict[str, Any] | None = None,
) -> dict[str, str]:
    """
    保存流式 Agent 的运行日志。
    """
    return save_agent_run(
        agent_name="CodeAgentStream",
        model_name=llm.model,
        status=status,
        user_message=user_message,
        answer=answer,
        max_steps=max_steps,
        steps=steps,
        error=error,
        pending_action=pending_action,
    )


def run_code_agent_stream(user_message: str, max_steps: int = 8):
    """
    CodeAgent 的 SSE 流式执行版本。

    注意：
    - 这是 step 流式，不是 token 流式
    - 每完成一个阶段 yield 一个 SSE data
    - 高风险工具会返回 approval_required 并停止
    """
    steps: list[dict[str, Any]] = []

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": CODE_AGENT_STREAM_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    yield sse_event(
        "agent_started",
        {
            "status": "running",
            "agent_name": "CodeAgentStream",
            "model_name": llm.model,
            "message": "CodeAgentStream 开始执行任务。",
            "user_message": user_message,
            "max_steps": max_steps,
            "created_at": now_iso(),
        },
    )

    for step_index in range(1, max_steps + 1):
        model_started_at = now_iso()
        model_start_time = time.perf_counter()

        yield sse_event(
            "model_call_started",
            {
                "step": step_index,
                "message": "开始调用模型。",
                "started_at": model_started_at,
            },
        )

        try:
            response = llm.client.chat.completions.create(
                model=llm.model,
                messages=messages,
                tools=CODE_AGENT_TOOLS,
                tool_choice="auto",
            )
        except Exception as e:
            model_ended_at = now_iso()

            error = {
                "type": ERROR_TYPE_MODEL,
                "message": "模型调用失败。",
                "detail": str(e),
            }

            step = {
                "step": step_index,
                "type": "model_call",
                "content": "模型调用失败。",
                "success": False,
                "error": error,
                "started_at": model_started_at,
                "ended_at": model_ended_at,
                "duration_ms": duration_ms(model_start_time),
            }
            steps.append(step)

            log_info = save_stream_log(
                status=AGENT_STATUS_FAILED,
                user_message=user_message,
                answer="模型调用失败，任务已停止。",
                max_steps=max_steps,
                steps=steps,
                error=error,
            )

            yield sse_event(
                "agent_failed",
                {
                    "status": AGENT_STATUS_FAILED,
                    "answer": "模型调用失败，任务已停止。",
                    "error": error,
                    "steps": steps,
                    **log_info,
                },
            )
            return

        message = response.choices[0].message
        tool_calls = message.tool_calls

        yield sse_event(
            "model_call_finished",
            {
                "step": step_index,
                "message": "模型调用完成。",
                "has_tool_calls": bool(tool_calls),
                "started_at": model_started_at,
                "ended_at": now_iso(),
                "duration_ms": duration_ms(model_start_time),
            },
        )

        # 情况 1：模型没有请求工具，说明已经给出最终回答
        if not tool_calls:
            answer = message.content or ""

            step = {
                "step": step_index,
                "type": "final_answer",
                "content": answer,
                "success": True,
                "error": None,
                "started_at": model_started_at,
                "ended_at": now_iso(),
                "duration_ms": duration_ms(model_start_time),
            }
            steps.append(step)

            log_info = save_stream_log(
                status=AGENT_STATUS_FINISHED,
                user_message=user_message,
                answer=answer,
                max_steps=max_steps,
                steps=steps,
                error=None,
            )

            yield sse_event(
                "final_answer",
                {
                    "step": step_index,
                    "content": answer,
                },
            )

            yield sse_event(
                "agent_finished",
                {
                    "status": AGENT_STATUS_FINISHED,
                    "answer": answer,
                    "steps": steps,
                    "error": None,
                    "pending_action": None,
                    **log_info,
                },
            )
            return

        # 情况 2：模型请求工具
        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": build_clean_tool_calls(tool_calls),
            }
        )

        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            risk_level = get_tool_risk_level(tool_name)

            # 高风险工具：不直接执行，进入审批
            if tool_requires_approval(tool_name):
                approval_started_at = now_iso()

                try:
                    tool_args = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError as e:
                    error = {
                        "type": ERROR_TYPE_TOOL,
                        "message": "工具参数不是合法 JSON。",
                        "detail": str(e),
                    }

                    step = {
                        "step": step_index,
                        "type": "approval_required",
                        "tool_name": tool_name,
                        "tool_args": None,
                        "risk_level": risk_level,
                        "tool_result": "工具参数解析失败，无法进入审批。",
                        "success": False,
                        "error": error,
                        "started_at": approval_started_at,
                        "ended_at": now_iso(),
                        "duration_ms": 0,
                    }
                    steps.append(step)

                    log_info = save_stream_log(
                        status=AGENT_STATUS_FAILED,
                        user_message=user_message,
                        answer="工具参数解析失败，任务已停止。",
                        max_steps=max_steps,
                        steps=steps,
                        error=error,
                    )

                    yield sse_event(
                        "agent_failed",
                        {
                            "status": AGENT_STATUS_FAILED,
                            "answer": "工具参数解析失败，任务已停止。",
                            "error": error,
                            "steps": steps,
                            **log_info,
                        },
                    )
                    return

                pending_action = save_pending_action(
                    agent_name="CodeAgentStream",
                    user_message=user_message,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    risk_level=risk_level,
                    reason=f"工具 {tool_name} 风险等级为 {risk_level}，需要用户确认后才能执行。",
                )

                step = {
                    "step": step_index,
                    "type": "approval_required",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "risk_level": risk_level,
                    "tool_result": "该工具需要用户确认，尚未执行。",
                    "content": "等待用户确认后再执行高风险工具。",
                    "success": None,
                    "error": None,
                    "started_at": approval_started_at,
                    "ended_at": now_iso(),
                    "duration_ms": 0,
                }
                steps.append(step)

                answer = f"CodeAgentStream 准备调用 {tool_name}，需要用户确认后才能继续。"

                log_info = save_stream_log(
                    status=AGENT_STATUS_WAITING_APPROVAL,
                    user_message=user_message,
                    answer=answer,
                    max_steps=max_steps,
                    steps=steps,
                    error=None,
                    pending_action=pending_action,
                )

                yield sse_event(
                    "approval_required",
                    {
                        "status": AGENT_STATUS_WAITING_APPROVAL,
                        "answer": answer,
                        "step": step,
                        "pending_action": pending_action,
                        "steps": steps,
                        **log_info,
                    },
                )
                return

            # 低风险 / 中风险工具：直接执行
            tool_started_at = now_iso()
            tool_start_time = time.perf_counter()

            yield sse_event(
                "tool_call_started",
                {
                    "step": step_index,
                    "tool_name": tool_name,
                    "risk_level": risk_level,
                    "message": f"开始执行工具：{tool_name}",
                    "started_at": tool_started_at,
                },
            )

            tool_execution = execute_tool(
                tool_call=tool_call,
                available_tools=(
                    AVAILABLE_CODE_AGENT_TOOLS
                ),
            )

            tool_ended_at = now_iso()

            step = {
                "step": step_index,
                "type": "tool_call",
                "tool_name": tool_execution["tool_name"],
                "tool_args": tool_execution["tool_args"],
                "risk_level": get_tool_risk_level(tool_execution["tool_name"]),
                "tool_result": tool_execution["tool_result"],
                "success": tool_execution["success"],
                "error": tool_execution["error"],
                "started_at": tool_started_at,
                "ended_at": tool_ended_at,
                "duration_ms": duration_ms(tool_start_time),
            }
            steps.append(step)

            yield sse_event(
                "tool_call_finished",
                {
                    "step": step,
                },
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_execution["tool_call_id"],
                    "content": tool_execution["tool_result"],
                }
            )

    # 达到最大步数
    answer = "CodeAgentStream 达到最大执行步数，已停止。请简化任务或增加 max_steps。"

    log_info = save_stream_log(
        status=AGENT_STATUS_MAX_STEPS_REACHED,
        user_message=user_message,
        answer=answer,
        max_steps=max_steps,
        steps=steps,
        error=None,
    )

    yield sse_event(
        "max_steps_reached",
        {
            "status": AGENT_STATUS_MAX_STEPS_REACHED,
            "answer": answer,
            "steps": steps,
            "error": None,
            "pending_action": None,
            **log_info,
        },
    )