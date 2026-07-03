TOOL_RISK_LOW = "low"
TOOL_RISK_MEDIUM = "medium"
TOOL_RISK_HIGH = "high"
TOOL_RISK_UNKNOWN = "unknown"


TOOL_RISK_LEVELS = {
    # 低风险：只读工具
    "list_files": TOOL_RISK_LOW,
    "read_file": TOOL_RISK_LOW,
    "read_file_lines": TOOL_RISK_LOW,
    "search_code": TOOL_RISK_LOW,
    "get_file_diff": TOOL_RISK_LOW,
    "get_workspace_diff": TOOL_RISK_LOW,
    "get_git_status": TOOL_RISK_LOW,
    "get_git_diff": TOOL_RISK_LOW,

    # 中风险：执行命令，但有白名单限制
    "run_command": TOOL_RISK_MEDIUM,

    # 高风险：会修改文件
    "edit_file": TOOL_RISK_HIGH,
    "write_new_file": TOOL_RISK_HIGH,
    "ensure_gitignore": TOOL_RISK_HIGH,
}


def get_tool_risk_level(tool_name: str) -> str:
    """
    获取工具风险等级。

    返回：
    - low
    - medium
    - high
    - unknown
    """
    return TOOL_RISK_LEVELS.get(tool_name, TOOL_RISK_UNKNOWN)


def tool_requires_approval(tool_name: str) -> bool:
    """
    判断工具是否需要用户确认。

    当前策略：
    - high 风险工具必须审批
    - low 风险工具不审批
    - medium 风险工具暂时不审批，但后续可扩展
    """
    return get_tool_risk_level(tool_name) == TOOL_RISK_HIGH