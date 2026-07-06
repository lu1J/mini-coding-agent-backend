from typing import Literal, Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """
    单轮聊天请求。
    """
    message: str = Field(..., min_length=1, description="用户输入的问题或任务")


class ChatResponse(BaseModel):
    """
    普通聊天响应。
    """
    answer: str = Field(..., description="模型生成的回答")


class ChatMessage(BaseModel):
    """
    多轮对话中的单条消息。
    """
    role: Literal["system", "user", "assistant"] = Field(
        ...,
        description="消息角色，只能是 system、user、assistant"
    )
    content: str = Field(..., min_length=1, description="消息内容")


class HistoryChatRequest(BaseModel):
    """
    多轮聊天请求。
    """
    messages: list[ChatMessage] = Field(
        ...,
        description="完整的历史对话消息"
    )


class AgentRequest(BaseModel):
    """
    Agent 任务请求。
    """
    message: str = Field(..., min_length=1, description="用户交给 Agent 的任务")
    max_steps: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Agent 最大执行步数，防止死循环"
    )


class AgentError(BaseModel):
    """
    Agent 错误信息。
    """
    type: str = Field(..., description="错误类型，例如 model_error、tool_error、unexpected_error")
    message: str = Field(..., description="用户可读的错误信息")
    detail: str | None = Field(default=None, description="详细错误信息，主要用于调试")


class PendingAction(BaseModel):
    """
    等待用户确认的工具动作。
    """
    approval_id: str = Field(..., description="确认 ID")
    agent_name: str = Field(default="", description="Agent 名称")
    user_message: str = Field(default="", description="用户原始任务")
    tool_name: str = Field(..., description="待执行工具名称")
    tool_args: dict[str, Any] = Field(default_factory=dict, description="待执行工具参数")
    risk_level: str = Field(default="high", description="待执行工具的风险等级")
    reason: str = Field(default="", description="需要确认的原因")
    status: str = Field(default="pending", description="待确认动作状态")
    created_at: str = Field(default="", description="创建时间")
    pending_path: str | None = Field(default=None, description="待确认动作保存路径")


class AgentStep(BaseModel):
    """
    Agent 执行过程中的单个步骤。
    """
    step: int = Field(..., description="第几轮执行")
    type: str = Field(..., description="步骤类型，例如 tool_call 或 final_answer")

    tool_name: str | None = Field(default=None, description="调用的工具名称")
    tool_args: dict[str, Any] | None = Field(default=None, description="工具参数")
    tool_result: str | None = Field(default=None, description="工具执行结果")

    risk_level: str | None = Field(default=None, description="工具风险等级，例如 low、medium、high")

    content: str | None = Field(default=None, description="最终回答内容或中间内容")

    success: bool | None = Field(default=None, description="该步骤是否执行成功")
    error: AgentError | None = Field(default=None, description="该步骤的错误信息")
    started_at: str | None = Field(default=None, description="步骤开始时间")
    ended_at: str | None = Field(default=None, description="步骤结束时间")
    duration_ms: int | None = Field(default=None, description="步骤耗时，单位毫秒")


class AgentResponse(BaseModel):
    """
    Agent 执行结果响应。
    """
    status: str = Field(..., description="执行状态，例如 finished、failed、max_steps_reached")
    answer: str = Field(..., description="Agent 最终回答")
    steps: list[AgentStep] = Field(default_factory=list, description="Agent 执行轨迹")

    error: AgentError | None = Field(default=None, description="错误信息，成功时为空")
    pending_action: PendingAction | None = Field(default=None, description="等待用户确认的动作")
    run_id: str | None = Field(default=None, description="本次 Agent 运行日志 ID")
    log_path: str | None = Field(default=None, description="运行日志保存路径")


class AgentRunSummary(BaseModel):
    """
    Agent 运行日志摘要。
    用于历史记录列表。
    """
    run_id: str = Field(..., description="运行 ID")
    agent_name: str = Field(default="", description="Agent 名称")
    model_name: str = Field(default="", description="模型名称")
    status: str = Field(default="", description="运行状态")
    user_message: str = Field(default="", description="用户任务")
    answer_preview: str = Field(default="", description="最终回答预览")
    created_at: str = Field(default="", description="创建时间")
    log_path: str = Field(default="", description="日志路径")

    error_type: str | None = Field(default=None, description="错误类型")
    error_message: str | None = Field(default=None, description="错误摘要")


class AgentRunListResponse(BaseModel):
    """
    Agent 运行日志列表响应。
    """
    total: int = Field(..., description="返回的运行记录数量")
    runs: list[AgentRunSummary] = Field(default_factory=list, description="运行记录列表")


class AgentRunDetail(BaseModel):
    """
    Agent 单次运行详情。
    """
    run_id: str = Field(..., description="运行 ID")
    agent_name: str = Field(default="", description="Agent 名称")
    model_name: str = Field(default="", description="模型名称")
    status: str = Field(default="", description="运行状态")
    user_message: str = Field(default="", description="用户任务")
    answer: str = Field(default="", description="最终回答")
    max_steps: int = Field(default=0, description="最大执行步数")
    steps: list[AgentStep] = Field(default_factory=list, description="完整执行轨迹")
    created_at: str = Field(default="", description="创建时间")
    log_path: str | None = Field(default=None, description="日志路径")

    error: AgentError | None = Field(default=None, description="错误信息")


class ApprovalExecuteRequest(BaseModel):
    """
    执行待确认动作的请求。
    """
    approved: bool = Field(..., description="是否批准执行")


class ApprovalExecuteResponse(BaseModel):
    """
    执行待确认动作后的响应。
    """
    status: str = Field(..., description="执行状态，例如 approved、rejected、not_found")
    message: str = Field(..., description="响应说明")
    approval_id: str = Field(..., description="确认 ID")

    tool_name: str | None = Field(default=None, description="工具名称")
    tool_args: dict[str, Any] | None = Field(default=None, description="工具参数")
    risk_level: str | None = Field(default=None, description="工具风险等级")

    tool_result: str | None = Field(default=None, description="工具执行结果")
    success: bool | None = Field(default=None, description="工具是否执行成功")
    error: AgentError | None = Field(default=None, description="错误信息")

    resume_result: dict[str, Any] | None = Field(
        default=None,
        description="审批执行后继续运行 Agent 的结果"
    )


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, description="会话标题")
    metadata: dict[str, Any] = Field(default_factory=dict, description="会话元数据")


class ConversationCreateResponse(BaseModel):
    conversation_id: str = Field(..., description="会话 ID")
    title: str = Field(..., description="会话标题")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")
    metadata: dict[str, Any] = Field(default_factory=dict, description="会话元数据")


class ConversationMessage(BaseModel):
    role: str = Field(..., description="消息角色：system/user/assistant/tool")
    content: str = Field(..., description="消息内容")
    created_at: str = Field(..., description="创建时间")
    metadata: dict[str, Any] = Field(default_factory=dict, description="消息元数据")


class ConversationListItem(BaseModel):
    conversation_id: str = Field(..., description="会话 ID")
    title: str = Field(..., description="会话标题")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")
    message_count: int = Field(..., description="消息数量")


class ConversationListResponse(BaseModel):
    conversations: list[ConversationListItem]


class ConversationDetailResponse(BaseModel):
    conversation_id: str = Field(..., description="会话 ID")
    title: str = Field(..., description="会话标题")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")
    metadata: dict[str, Any] = Field(default_factory=dict, description="会话元数据")
    messages: list[ConversationMessage]


class ConversationAppendMessageRequest(BaseModel):
    role: str = Field(..., description="消息角色：system/user/assistant/tool")
    content: str = Field(..., description="消息内容")
    metadata: dict[str, Any] = Field(default_factory=dict, description="消息元数据")


class ConversationAppendMessageResponse(BaseModel):
    conversation_id: str = Field(..., description="会话 ID")
    message: ConversationMessage


class MemoryChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户消息")
    conversation_id: str | None = Field(default=None, description="会话 ID，不传则自动创建")
    title: str | None = Field(default=None, description="新会话标题")
    max_tokens: int = Field(default=800, ge=1, le=4000, description="模型最大输出 token 数")
    max_history_messages: int = Field(default=20, ge=1, le=100, description="最多携带最近多少条历史消息")
    metadata: dict[str, Any] = Field(default_factory=dict, description="用户消息元数据")


class MemoryChatResponse(BaseModel):
    conversation_id: str = Field(..., description="会话 ID")
    title: str = Field(..., description="会话标题")
    answer: str = Field(..., description="模型回复")
    message_count: int = Field(..., description="当前会话消息数量")