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
from openai import APIConnectionError, APIError, APIStatusError
from app.memory.context_manager import build_context_window
from app.agent.task_planner import build_task_plan

from app.memory.conversation_store import (
    create_conversation as store_create_conversation,
    list_conversations as store_list_conversations,
    read_conversation as store_read_conversation,
    append_message as store_append_message,
    create_or_read_conversation as store_create_or_read_conversation,
    get_conversation_summary as store_get_conversation_summary,
    save_conversation_summary as store_save_conversation_summary,
)

from app.memory.summary_manager import (
    build_summary_prompt_messages,
    get_existing_summary_content,
    get_unsummarized_messages,
)

from app.schemas import (
    ChatRequest,
    ChatResponse,
    HistoryChatRequest,
    AgentRequest,
    AgentResponse,
    TaskPlanRequest,
    TaskPlanResponse,
    AgentRunListResponse,
    AgentRunDetail,
    ApprovalExecuteRequest,
    ApprovalExecuteResponse,
    ConversationCreateRequest,
    ConversationCreateResponse,
    ConversationListResponse,
    ConversationDetailResponse,
    ConversationAppendMessageRequest,
    ConversationAppendMessageResponse,
    MemoryChatRequest,
    MemoryChatResponse,
    ConversationSummaryResponse,
    ConversationUpdateSummaryRequest,
    ConversationUpdateSummaryResponse,
    ConversationRefreshSummaryRequest,
    ConversationRefreshSummaryResponse,
)

app = FastAPI(title="Mini Agent Backend")


MEMORY_CHAT_SYSTEM_PROMPT = """
你是一个支持多轮会话记忆的 AI 助手。

你需要根据当前用户问题和已有历史消息进行回答。
如果历史消息中包含用户之前提供的信息，你可以自然地结合上下文。
不要编造历史中没有出现过的事实。
如果上下文不足，请直接说明需要更多信息。
""".strip()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/conversations", response_model=ConversationCreateResponse)
def create_conversation_api(request: ConversationCreateRequest):
    """
    创建一个新会话。
    """
    conversation = store_create_conversation(
        title=request.title,
        metadata=request.metadata,
    )

    return conversation


@app.get("/conversations", response_model=ConversationListResponse)
def list_conversations_api(limit: int = 20):
    """
    查看最近会话列表。
    """
    safe_limit = max(1, min(limit, 100))

    conversations = store_list_conversations(limit=safe_limit)

    return {
        "conversations": conversations,
    }


@app.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def read_conversation_api(conversation_id: str):
    """
    查看某个会话详情。
    """
    try:
        return store_read_conversation(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/conversations/{conversation_id}/summary",
    response_model=ConversationSummaryResponse,
)
def get_conversation_summary_api(conversation_id: str):
    """
    读取某个会话的摘要记忆。
    """
    try:
        summary = store_get_conversation_summary(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "conversation_id": conversation_id,
        "summary": summary,
    }


@app.put(
    "/conversations/{conversation_id}/summary",
    response_model=ConversationUpdateSummaryResponse,
)
def update_conversation_summary_api(
    conversation_id: str,
    request: ConversationUpdateSummaryRequest,
):
    """
    保存或更新某个会话的摘要记忆。
    """
    try:
        summary = store_save_conversation_summary(
            conversation_id=conversation_id,
            content=request.content,
            source_message_count=request.source_message_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "conversation_id": conversation_id,
        "summary": summary,
    }


@app.post(
    "/conversations/{conversation_id}/summary/refresh",
    response_model=ConversationRefreshSummaryResponse,
)
def refresh_conversation_summary_api(
    conversation_id: str,
    request: ConversationRefreshSummaryRequest,
):
    """
    自动刷新某个会话的摘要记忆。

    当前逻辑：
    - force=False：只摘要还没有被 summary 覆盖的新消息
    - force=True：从头重新摘要整个会话
    """
    try:
        conversation = store_read_conversation(conversation_id)

        new_messages, start_index, target_source_message_count = get_unsummarized_messages(
            conversation=conversation,
            max_new_messages=request.max_new_messages,
            force=request.force,
        )

        if not new_messages:
            summary = store_get_conversation_summary(conversation_id)

            return {
                "conversation_id": conversation_id,
                "summary": summary,
                "refreshed": False,
                "processed_message_count": 0,
                "source_message_count": summary.get("source_message_count", 0),
                "reason": "没有需要摘要的新消息。",
            }

        existing_summary = "" if request.force else get_existing_summary_content(conversation)

        summary_prompt_messages = build_summary_prompt_messages(
            existing_summary=existing_summary,
            new_messages=new_messages,
            start_index=start_index,
        )

        summary_content = llm.chat(
            messages=summary_prompt_messages,
            max_tokens=request.max_tokens,
        )

        summary = store_save_conversation_summary(
            conversation_id=conversation_id,
            content=summary_content,
            source_message_count=target_source_message_count,
        )

        return {
            "conversation_id": conversation_id,
            "summary": summary,
            "refreshed": True,
            "processed_message_count": len(new_messages),
            "source_message_count": summary["source_message_count"],
            "reason": None,
        }

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except APIConnectionError as exc:
        raise HTTPException(
            status_code=502,
            detail="LLM 连接失败，请检查网络、代理或 DeepSeek 配置。",
        ) from exc
    except APIStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LLM 服务返回错误：HTTP {exc.status_code}",
        ) from exc
    except APIError as exc:
        raise HTTPException(
            status_code=502,
            detail="LLM 调用失败，请稍后重试。",
        ) from exc


@app.post(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationAppendMessageResponse,
)
def append_conversation_message_api(
    conversation_id: str,
    request: ConversationAppendMessageRequest,
):
    """
    向某个会话追加一条消息。
    """
    try:
        message = store_append_message(
            conversation_id=conversation_id,
            role=request.role,
            content=request.content,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "conversation_id": conversation_id,
        "message": message,
    }


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


@app.post("/chat/memory", response_model=MemoryChatResponse)
def chat_with_memory(request: MemoryChatRequest):
    """
    带会话记忆的聊天接口。

    - 如果 request.conversation_id 为空：创建新会话
    - 如果 request.conversation_id 不为空：读取已有会话
    - 保存用户消息
    - 携带最近历史消息调用 LLM
    - 保存助手回复
    """
    try:
        conversation = store_create_or_read_conversation(
            conversation_id=request.conversation_id,
            title=request.title or "Memory Chat",
            metadata={
                "source": "chat_memory",
            },
        )

        conversation_id = conversation["conversation_id"]

        store_append_message(
            conversation_id=conversation_id,
            role="user",
            content=request.message,
            metadata=request.metadata,
        )

        current_conversation = store_read_conversation(conversation_id)
        summary = current_conversation.get("summary", {})
        summary_content = ""

        if isinstance(summary, dict):
            summary_content = summary.get("content", "")

        context = build_context_window(
            raw_messages=current_conversation.get("messages", []),
            system_message=MEMORY_CHAT_SYSTEM_PROMPT,
            summary_message=summary_content,
            max_history_messages=request.max_history_messages,
            max_context_tokens=request.max_context_tokens,
            reserved_output_tokens=request.max_tokens,
        )

        messages = context["messages"]

        answer = llm.chat(
            messages=messages,
            max_tokens=request.max_tokens,
        )

        store_append_message(
            conversation_id=conversation_id,
            role="assistant",
            content=answer,
            metadata={
                "source": "llm",
            },
        )

        updated_conversation = store_read_conversation(conversation_id)

        return {
            "conversation_id": conversation_id,
            "title": updated_conversation["title"],
            "answer": answer,
            "message_count": len(updated_conversation.get("messages", [])),
            "context_stats": {
                "total_messages": context["total_messages"],
                "used_messages": context["used_messages"],
                "dropped_messages": context["dropped_messages"],
                "estimated_input_tokens": context["estimated_input_tokens"],
                "max_context_tokens": context["max_context_tokens"],
                "reserved_output_tokens": context["reserved_output_tokens"],
                "summary_used": context["summary_used"],
            },
        }


    except ValueError as exc:

        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except FileNotFoundError as exc:

        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except APIConnectionError as exc:

        raise HTTPException(

            status_code=502,

            detail="LLM 连接失败，请检查网络、代理或 DeepSeek 配置。",

        ) from exc

    except APIStatusError as exc:

        raise HTTPException(

            status_code=502,

            detail=f"LLM 服务返回错误：HTTP {exc.status_code}",

        ) from exc

    except APIError as exc:

        raise HTTPException(

            status_code=502,

            detail="LLM 调用失败，请稍后重试。",

        ) from exc


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

@app.post("/agent/plan", response_model=TaskPlanResponse)
def create_agent_plan(request: TaskPlanRequest):
    """
    根据用户任务生成结构化执行计划。

    注意：
    这个接口只做规划，不执行工具，不修改文件，不调用模型。
    """
    return build_task_plan(request.message)

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