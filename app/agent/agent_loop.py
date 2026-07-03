import json
import time
from datetime import datetime
from typing import Any, Callable

from app.agent.run_logger import save_agent_run
from app.agent.status import (
    AGENT_STATUS_FINISHED,
    AGENT_STATUS_FAILED,
    AGENT_STATUS_MAX_STEPS_REACHED,
    AGENT_STATUS_WAITING_APPROVAL,
    ERROR_TYPE_MODEL,
    ERROR_TYPE_TOOL,
)
from app.llm.deepseek_client import llm
from app.agent.approval_store import save_pending_action

from app.agent.tool_policy import get_tool_risk_level, tool_requires_approval


def now_iso() -> str:
    """
    返回当前时间字符串，用于记录步骤开始和结束时间。
    """
    return datetime.now().isoformat(timespec="seconds")


def duration_ms(start_time: float) -> int:
    """
    根据开始时间计算耗时，单位毫秒。
    """
    return int((time.perf_counter() - start_time) * 1000)


def build_clean_tool_calls(tool_calls) -> list[dict[str, Any]]:
    """
    把模型返回的 tool_calls 转成干净的 dict。
    """
    clean_tool_calls = []

    for tool_call in tool_calls:
        clean_tool_calls.append({
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function.name,
                "arguments": tool_call.function.arguments,
            }
        })

    return clean_tool_calls


def execute_tool(
    tool_call,
    available_tools: dict[str, Callable[..., Any]],
    agent_name: str = "Agent",
) -> dict[str, Any]:
    """
    执行单个工具调用。

    返回内容包括：
    - tool_call_id
    - tool_name
    - tool_args
    - tool_result
    - success
    - error
    """
    tool_name = tool_call.function.name
    tool_args_json = tool_call.function.arguments

    try:
        tool_args = json.loads(tool_args_json)
    except json.JSONDecodeError as e:
        return {
            "tool_call_id": tool_call.id,
            "tool_name": tool_name,
            "tool_args": {},
            "tool_result": "工具参数不是合法 JSON，未执行工具。",
            "success": False,
            "error": {
                "type": ERROR_TYPE_TOOL,
                "message": "工具参数解析失败。",
                "detail": str(e),
            }
        }

    tool_func = available_tools.get(tool_name)

    if not tool_func:
        return {
            "tool_call_id": tool_call.id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_result": f"错误：{agent_name} 没有可用工具：{tool_name}",
            "success": False,
            "error": {
                "type": ERROR_TYPE_TOOL,
                "message": f"{agent_name} 没有可用工具：{tool_name}",
                "detail": None,
            }
        }

    try:
        raw_result = tool_func(**tool_args)

        # 情况 1：工具返回结构化结果
        if isinstance(raw_result, dict) and "success" in raw_result:
            tool_result_text = str(raw_result.get("result", ""))
            tool_success = bool(raw_result.get("success"))
            tool_error = raw_result.get("error")

            return {
                "tool_call_id": tool_call.id,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_result": tool_result_text,
                "success": tool_success,
                "error": tool_error,
            }

        # 情况 2：旧工具仍然返回普通字符串
        return {
            "tool_call_id": tool_call.id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_result": str(raw_result),
            "success": True,
            "error": None,
        }

    except Exception as e:
        return {
            "tool_call_id": tool_call.id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "tool_result": f"工具执行失败：{str(e)}",
            "success": False,
            "error": {
                "type": ERROR_TYPE_TOOL,
                "message": "工具执行失败。",
                "detail": str(e),
            }
        }


def build_result_with_log(
    *,
    agent_name: str,
    user_message: str,
    status: str,
    answer: str,
    steps: list[dict[str, Any]],
    max_steps: int,
    error: dict[str, Any] | None = None,
    pending_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    构建 Agent 返回结果，并保存运行日志。
    """
    result = {
        "status": status,
        "answer": answer,
        "steps": steps,
        "error": error,
        "pending_action": pending_action,
    }

    try:
        log_info = save_agent_run(
            agent_name=agent_name,
            user_message=user_message,
            status=status,
            answer=answer,
            steps=steps,
            max_steps=max_steps,
            model_name=llm.model,
            error=error,
            pending_action=pending_action,
        )
        result.update(log_info)
    except Exception as e:
        result["log_error"] = f"日志保存失败：{str(e)}"

    return result


def run_agent_loop(
    *,
    agent_name: str,
    system_prompt: str,
    tools: list[dict[str, Any]],
    available_tools: dict[str, Callable[..., Any]],
    user_message: str,
    max_steps: int = 8,
) -> dict[str, Any]:
    """
    通用 Agent Loop。
    """

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": user_message,
        }
    ]

    steps = []

    for step_index in range(1, max_steps + 1):
        model_started_at = now_iso()
        model_start_time = time.perf_counter()

        try:
            response = llm.client.chat.completions.create(
                model=llm.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
        except Exception as e:
            model_ended_at = now_iso()

            error = {
                "type": ERROR_TYPE_MODEL,
                "message": "模型调用失败，任务已停止。",
                "detail": str(e),
            }

            steps.append({
                "step": step_index,
                "type": "model_call",
                "success": False,
                "error": error,
                "started_at": model_started_at,
                "ended_at": model_ended_at,
                "duration_ms": duration_ms(model_start_time),
                "content": "模型调用失败。",
            })

            return build_result_with_log(
                agent_name=agent_name,
                user_message=user_message,
                status=AGENT_STATUS_FAILED,
                answer="模型调用失败，任务已停止。请检查模型配置、API Key、网络连接或稍后重试。",
                steps=steps,
                max_steps=max_steps,
                error=error,
            )

        model_ended_at = now_iso()
        model_duration = duration_ms(model_start_time)

        assistant_message = response.choices[0].message

        # 情况 1：模型没有继续请求工具，说明准备最终回答
        if not assistant_message.tool_calls:
            final_answer = assistant_message.content or ""

            steps.append({
                "step": step_index,
                "type": "final_answer",
                "content": final_answer,
                "success": True,
                "error": None,
                "started_at": model_started_at,
                "ended_at": model_ended_at,
                "duration_ms": model_duration,
            })

            return build_result_with_log(
                agent_name=agent_name,
                user_message=user_message,
                status=AGENT_STATUS_FINISHED,
                answer=final_answer,
                steps=steps,
                max_steps=max_steps,
            )

        # 情况 2：模型请求调用工具
        clean_tool_calls = build_clean_tool_calls(assistant_message.tool_calls)

        messages.append({
            "role": "assistant",
            "content": assistant_message.content or "",
            "tool_calls": clean_tool_calls,
        })

        for tool_call in assistant_message.tool_calls:
            tool_name = tool_call.function.name
            risk_level = get_tool_risk_level(tool_name)

            if tool_requires_approval(tool_name):
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as e:
                    error = {
                        "type": ERROR_TYPE_TOOL,
                        "message": "工具参数解析失败，无法进入确认流程。",
                        "detail": str(e),
                    }

                    steps.append({
                        "step": step_index,
                        "type": "approval_required",
                        "tool_name": tool_name,
                        "tool_args": {},
                        "tool_result": "工具参数不是合法 JSON。",
                        "success": False,
                        "error": error,
                        "started_at": now_iso(),
                        "ended_at": now_iso(),
                        "duration_ms": 0,
                    })

                    return build_result_with_log(
                        agent_name=agent_name,
                        user_message=user_message,
                        status=AGENT_STATUS_FAILED,
                        answer="工具参数解析失败，任务已停止。",
                        steps=steps,
                        max_steps=max_steps,
                        error=error,
                    )

                pending_action = save_pending_action(
                    agent_name=agent_name,
                    user_message=user_message,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    risk_level=risk_level,
                    reason=f"工具 {tool_name} 风险等级为 {risk_level}，需要用户确认后才能执行。",
                )

                steps.append({
                    "step": step_index,
                    "type": "approval_required",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "risk_level": risk_level,
                    "tool_result": "该工具需要用户确认，尚未执行。",
                    "content": "等待用户确认后再执行高风险工具。",
                    "success": None,
                    "error": None,
                    "started_at": now_iso(),
                    "ended_at": now_iso(),
                    "duration_ms": 0,
                })

                return build_result_with_log(
                    agent_name=agent_name,
                    user_message=user_message,
                    status=AGENT_STATUS_WAITING_APPROVAL,
                    answer=f"{agent_name} 准备调用 {tool_name} 修改文件，需要用户确认后才能继续。",
                    steps=steps,
                    max_steps=max_steps,
                    pending_action=pending_action,
                )
            tool_started_at = now_iso()
            tool_start_time = time.perf_counter()

            tool_execution = execute_tool(
                tool_call=tool_call,
                available_tools=available_tools,
                agent_name=agent_name,
            )

            tool_ended_at = now_iso()

            executed_tool_name = tool_execution["tool_name"]
            executed_risk_level = get_tool_risk_level(executed_tool_name)

            steps.append({
                "step": step_index,
                "type": "tool_call",
                "tool_name": executed_tool_name,
                "tool_args": tool_execution["tool_args"],
                "risk_level": executed_risk_level,
                "tool_result": tool_execution["tool_result"],
                "success": tool_execution["success"],
                "error": tool_execution["error"],
                "started_at": tool_started_at,
                "ended_at": tool_ended_at,
                "duration_ms": duration_ms(tool_start_time),
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tool_execution["tool_call_id"],
                "content": tool_execution["tool_result"],
            })

    answer = f"{agent_name} 达到最大执行步数，已停止。请简化任务或增加 max_steps。"

    return build_result_with_log(
        agent_name=agent_name,
        user_message=user_message,
        status=AGENT_STATUS_MAX_STEPS_REACHED,
        answer=answer,
        steps=steps,
        max_steps=max_steps,
    )