from typing import Any


REFLECTION_TRIGGER_TOOL_ERROR = "tool_error"
REFLECTION_TRIGGER_MAX_STEPS = "max_steps_reached"
REFLECTION_TRIGGER_APPROVAL_REJECTED = "approval_rejected"
REFLECTION_TRIGGER_MODEL_ERROR = "model_error"
REFLECTION_TRIGGER_UNEXPECTED_ERROR = "unexpected_error"


def normalize_text(value: Any) -> str:
    """
    将任意值安全转换成字符串。

    这个函数的作用是：
    - 避免 None 报错
    - 避免 error/detail 不是字符串时处理失败
    - 统一去掉首尾空白
    """
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


def extract_error_type(tool_result: dict[str, Any] | None = None, error: Any = None) -> str:
    """
    从工具结果或异常信息中提取错误类型。

    为什么需要这个函数？
    因为不同工具返回的错误结构可能不完全一样。

    可能的情况：
    1. tool_result["error"] 是 dict
    2. tool_result["error"] 是 str
    3. tool_result 里直接有 type
    4. 只有 error 字符串
    """
    if isinstance(tool_result, dict):
        error_obj = tool_result.get("error")

        if isinstance(error_obj, dict):
            error_type = normalize_text(error_obj.get("type"))
            if error_type:
                return error_type

        direct_type = normalize_text(tool_result.get("type"))
        if direct_type:
            return direct_type

    error_text = normalize_text(error).lower()

    if "not found" in error_text or "不存在" in error_text:
        return "file_not_found"

    if "permission" in error_text or "denied" in error_text or "权限" in error_text:
        return "permission_denied"

    if "command" in error_text or "退出码" in error_text or "stderr" in error_text:
        return "command_failed"

    if error_text:
        return "unknown_error"

    return "unknown_error"


def extract_error_message(tool_result: dict[str, Any] | None = None, error: Any = None) -> str:
    """
    从工具结果中提取可读的错误信息。

    这个函数是给 reflection 使用的。
    reflection 不需要完整 tool_result，只需要知道失败原因。
    """
    if isinstance(tool_result, dict):
        error_obj = tool_result.get("error")

        if isinstance(error_obj, dict):
            message = normalize_text(error_obj.get("message"))
            detail = normalize_text(error_obj.get("detail"))

            if message and detail:
                return f"{message} {detail}"

            if message:
                return message

            if detail:
                return detail

        if isinstance(error_obj, str):
            return normalize_text(error_obj)

        stderr = normalize_text(tool_result.get("stderr"))
        if stderr:
            return stderr

        message = normalize_text(tool_result.get("message"))
        if message:
            return message

    return normalize_text(error)


def build_suggestion_by_error_type(error_type: str, tool_name: str | None = None) -> tuple[str, str | None, bool]:
    """
    根据错误类型生成建议。

    返回三个值：
    - suggestion：给人的建议
    - next_action_hint：建议下一步可能调用的工具
    - can_retry：是否值得重试
    """
    safe_tool_name = normalize_text(tool_name)

    if error_type == "file_not_found":
        return (
            "目标文件或路径不存在。建议先查看 workspace 文件结构，确认文件路径是否正确。",
            "list_files",
            True,
        )

    if error_type == "permission_denied":
        return (
            "当前操作可能被路径安全策略或权限限制拦截。建议检查路径是否在 workspace 内，以及是否需要用户审批。",
            None,
            False,
        )

    if error_type == "command_failed":
        return (
            "命令执行失败。建议查看 stderr 或先读取相关文件，确认文件路径、语法和命令参数是否正确。",
            "read_file_lines",
            True,
        )

    if error_type == "validation_error":
        return (
            "工具参数校验失败。建议重新检查 tool_args，确认必填字段、路径和参数类型是否正确。",
            None,
            True,
        )

    if error_type == "model_output_invalid":
        return (
            "模型输出格式不符合 Agent 预期。建议重新提示模型按规定 JSON/tool_call 格式输出。",
            None,
            True,
        )

    if safe_tool_name:
        return (
            f"工具 {safe_tool_name} 执行失败。建议根据错误信息重新检查参数，并选择更安全的只读工具进行确认。",
            None,
            True,
        )

    return (
        "发生未知错误。建议查看错误详情、执行步骤和工具参数，再决定是否重试。",
        None,
        False,
    )


def build_tool_error_reflection(
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
    tool_result: dict[str, Any] | None = None,
    error: Any = None,
) -> dict[str, Any]:
    """
    构建工具失败时的 reflection。

    这是 v0.5.0 最核心的函数之一。

    输入：
    - tool_name：哪个工具失败了
    - tool_args：当时传给工具的参数
    - tool_result：工具返回结果
    - error：额外错误信息

    输出：
    一个结构化 reflection dict。
    """
    error_type = extract_error_type(tool_result=tool_result, error=error)
    error_message = extract_error_message(tool_result=tool_result, error=error)

    suggestion, next_action_hint, can_retry = build_suggestion_by_error_type(
        error_type=error_type,
        tool_name=tool_name,
    )

    return {
        "trigger": REFLECTION_TRIGGER_TOOL_ERROR,
        "failed_tool": tool_name,
        "tool_args": tool_args or {},
        "error_type": error_type,
        "error_message": error_message,
        "analysis": f"工具 {tool_name} 执行失败，错误类型为 {error_type}。",
        "suggestion": suggestion,
        "can_retry": can_retry,
        "next_action_hint": next_action_hint,
    }


def build_max_steps_reflection(
    max_steps: int,
    completed_steps: int,
    user_message: str | None = None,
) -> dict[str, Any]:
    """
    构建 max_steps 用完时的 reflection。

    这种情况不一定是工具失败，
    可能是任务太复杂，或者 max_steps 设置太小。
    """
    safe_user_message = normalize_text(user_message)

    if safe_user_message:
        analysis = (
            f"Agent 已执行 {completed_steps} 步，达到 max_steps={max_steps} 限制，"
            f"但任务可能尚未完全完成。当前任务是：{safe_user_message}"
        )
    else:
        analysis = (
            f"Agent 已执行 {completed_steps} 步，达到 max_steps={max_steps} 限制，"
            "但任务可能尚未完全完成。"
        )

    return {
        "trigger": REFLECTION_TRIGGER_MAX_STEPS,
        "failed_tool": None,
        "tool_args": {},
        "error_type": "max_steps_reached",
        "error_message": f"达到最大步骤限制：{max_steps}",
        "analysis": analysis,
        "suggestion": "建议增加 max_steps，或将复杂任务拆分成更小的子任务。",
        "can_retry": True,
        "next_action_hint": None,
    }


def build_approval_rejected_reflection(
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    构建用户拒绝审批时的 reflection。

    高风险工具被拒绝后，Agent 不应该继续强行写文件。
    """
    return {
        "trigger": REFLECTION_TRIGGER_APPROVAL_REJECTED,
        "failed_tool": tool_name,
        "tool_args": tool_args or {},
        "error_type": "approval_rejected",
        "error_message": "用户拒绝了高风险操作审批。",
        "analysis": f"高风险工具 {tool_name} 未被用户批准，因此不能继续执行该写操作。",
        "suggestion": "建议改为只读分析，或者让用户确认是否需要修改更安全的目标文件。",
        "can_retry": False,
        "next_action_hint": None,
    }


def build_model_error_reflection(error: Any) -> dict[str, Any]:
    """
    构建模型调用或模型输出错误时的 reflection。
    """
    error_message = normalize_text(error)

    return {
        "trigger": REFLECTION_TRIGGER_MODEL_ERROR,
        "failed_tool": None,
        "tool_args": {},
        "error_type": "model_error",
        "error_message": error_message,
        "analysis": "模型调用或模型输出解析失败，Agent 无法继续稳定执行。",
        "suggestion": "建议检查模型服务、网络代理、API Key，或重新约束模型输出格式。",
        "can_retry": True,
        "next_action_hint": None,
    }


def should_retry_from_reflection(reflection: dict[str, Any], retry_count: int, max_retries: int = 1) -> bool:
    """
    判断是否允许根据 reflection 重试。

    为什么要限制重试次数？
    因为 Agent 如果失败后无限重试，会造成死循环和大量模型调用。

    当前策略：
    - reflection.can_retry 必须为 True
    - retry_count 必须小于 max_retries
    """
    can_retry = bool(reflection.get("can_retry"))

    if not can_retry:
        return False

    return retry_count < max_retries