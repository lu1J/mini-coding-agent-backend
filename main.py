import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from app.llm.deepseek_client import llm
from app.agent.tool_runner import run_time_tool_agent
from app.agent.hello_agent import run_hello_agent
from app.agent.code_agent import run_code_agent
from app.agent.run_logger import list_agent_runs, read_agent_run
from app.agent.approval_store import read_pending_action, delete_pending_action
from app.tools.file_tools import AVAILABLE_FILE_TOOLS
from app.agent.agent_stream import run_code_agent_stream

from app.schemas import (
    ChatRequest,
    ChatResponse,
    HistoryChatRequest,
    AgentRequest,
    AgentResponse,
    AgentRunListResponse,
    AgentRunDetail,
    ApprovalExecuteRequest,
    ApprovalExecuteResponse,
)

app = FastAPI(title="Mini Agent Backend")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """
    单轮聊天接口。
    用户只传 message，后端自动组装 messages。
    """
    messages = [
        {
            "role": "system",
            "content": "你是一个耐心的 AI 编程老师，擅长用小白能听懂的方式讲解。"
        },
        {
            "role": "user",
            "content": req.message
        }
    ]

    answer = llm.chat(messages)
    return ChatResponse(answer=answer)


@app.post("/chat/history", response_model=ChatResponse)
def chat_with_history(req: HistoryChatRequest):
    """
    多轮对话接口。
    用户直接传完整 messages，后端把完整历史交给模型。
    """
    messages = [
        {
            "role": item.role,
            "content": item.content
        }
        for item in req.messages
    ]

    answer = llm.chat(messages)
    return ChatResponse(answer=answer)


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    """
    单轮流式聊天接口。
    """
    messages = [
        {
            "role": "system",
            "content": "你是一个耐心的 AI 编程老师，擅长用小白能听懂的方式讲解。"
        },
        {
            "role": "user",
            "content": req.message
        }
    ]

    def event_generator():
        for content in llm.stream_chat(messages):
            data = {
                "content": content
            }
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )

@app.post("/agent/time")
def agent_time(req: ChatRequest):
    """
    Tool Calling 示例接口。
    用户问时间，模型会调用 get_current_time 工具。
    """
    return run_time_tool_agent(req.message)

@app.post(
    "/agent/hello",
    response_model=AgentResponse,
    response_model_exclude_none=True
)
def hello_agent(req: AgentRequest):
    """
    HelloAgent 示例接口。
    支持多轮工具调用循环。
    """
    return run_hello_agent(
        user_message=req.message,
        max_steps=req.max_steps
    )

@app.post(
    "/agent/code",
    response_model=AgentResponse,
    response_model_exclude_none=True
)
def code_agent(req: AgentRequest):
    """
    CodeAgent 正式接口。
    用于执行代码阅读、搜索、修改、diff、检查等任务。
    """
    return run_code_agent(
        user_message=req.message,
        max_steps=req.max_steps
    )

@app.post("/agent/code/stream")
def code_agent_stream(req: AgentRequest):
    return StreamingResponse(
        run_code_agent_stream(
            user_message=req.message,
            max_steps=req.max_steps,
        ),
        media_type="text/event-stream",
    )

@app.get("/agent/runs", response_model=AgentRunListResponse)
def get_agent_runs(limit: int = 20):
    """
    查看最近的 Agent 运行记录列表。
    """
    runs = list_agent_runs(limit=limit)

    return {
        "total": len(runs),
        "runs": runs,
    }


@app.get("/agent/runs/{run_id}", response_model=AgentRunDetail)
def get_agent_run_detail(run_id: str):
    """
    查看某一次 Agent 运行详情。
    """
    try:
        run_data = read_agent_run(run_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not run_data:
        raise HTTPException(status_code=404, detail="运行日志不存在")

    return run_data


@app.post("/agent/approvals/{approval_id}/execute", response_model=ApprovalExecuteResponse)
def execute_approval(approval_id: str, req: ApprovalExecuteRequest):
    """
    执行或拒绝一个等待确认的工具动作。

    第一版只负责执行被拦截的工具，不自动续跑 Agent Loop。
    """
    try:
        pending_action = read_pending_action(approval_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    risk_level = pending_action.get("risk_level", "high")

    if not pending_action:
        raise HTTPException(status_code=404, detail="待确认动作不存在或已处理")

    if not req.approved:
        delete_pending_action(approval_id)

        return {
            "status": "rejected",
            "message": "用户拒绝执行该动作，待确认动作已删除。",
            "approval_id": approval_id,
            "tool_name": pending_action.get("tool_name"),
            "tool_args": pending_action.get("tool_args"),
            "risk_level": risk_level,
            "tool_result": None,
            "success": False,
            "error": None,
        }

    tool_name = pending_action.get("tool_name")
    tool_args = pending_action.get("tool_args") or {}
    risk_level = pending_action.get("risk_level", "high")

    tool_func = AVAILABLE_FILE_TOOLS.get(tool_name)

    if not tool_func:
        return {
            "status": "failed",
            "message": f"工具不存在：{tool_name}",
            "approval_id": approval_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "risk_level": risk_level,
            "tool_result": None,
            "success": False,
            "error": {
                "type": "tool_error",
                "message": f"工具不存在：{tool_name}",
                "detail": None,
            },
        }

    try:
        tool_result = tool_func(**tool_args)
        delete_pending_action(approval_id)

        # 审批通过并执行工具后，重新构造一个续跑任务，让 CodeAgent 继续完成原始任务
        resume_result = None

        try:
            from app.agent.code_agent import run_code_agent

            original_user_message = pending_action.get("user_message", "")

            resume_message = (
                "下面是一个已经经过用户确认并执行完成的高风险工具动作。\n\n"
                f"用户原始任务：\n{original_user_message}\n\n"
                f"已执行工具：{tool_name}\n\n"
                f"工具参数：\n{tool_args}\n\n"
                f"工具执行结果：\n{str(tool_result)}\n\n"
                "请基于以上结果继续完成用户原始任务。\n"
                "不要重复调用已经执行过的写入工具。\n"
                "如果需要审查改动，可以调用 get_git_status、get_git_diff。\n"
                "如果需要检查 Python 语法，可以调用 run_command。\n"
                "最后请总结：已执行了什么、结果是否成功、当前还需要用户注意什么。"
            )

            resume_result = run_code_agent(
                user_message=resume_message,
                max_steps=5,
            )

        except Exception as e:
            resume_result = {
                "status": "failed",
                "answer": "审批动作已执行，但后续 Agent Resume 失败。",
                "steps": [],
                "error": {
                    "type": "resume_error",
                    "message": "审批后续跑失败。",
                    "detail": str(e),
                }
            }

        return {
            "status": "approved",
            "message": "用户已确认，工具已执行，并已尝试继续运行 Agent。",
            "approval_id": approval_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "risk_level": risk_level,
            "tool_result": str(tool_result),
            "success": True,
            "error": None,
            "resume_result": resume_result,
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": "工具执行失败。",
            "approval_id": approval_id,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "risk_level": risk_level,
            "tool_result": None,
            "success": False,
            "error": {
                "type": "tool_error",
                "message": "工具执行失败。",
                "detail": str(e),
            },
        }