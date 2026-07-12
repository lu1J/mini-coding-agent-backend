import re
from typing import Any

from app.agent.tool_policy import get_tool_risk_level


TASK_INTENT_READ = "read"
TASK_INTENT_SEARCH = "search"
TASK_INTENT_ANALYZE = "analyze"
TASK_INTENT_EDIT = "edit"
TASK_INTENT_TEST = "test"
TASK_INTENT_GIT = "git"
TASK_INTENT_PLAN = "plan"

TASK_COMPLEXITY_SIMPLE = "simple"
TASK_COMPLEXITY_MEDIUM = "medium"
TASK_COMPLEXITY_COMPLEX = "complex"

TASK_RISK_LOW = "low"
TASK_RISK_MEDIUM = "medium"
TASK_RISK_HIGH = "high"


INTENT_KEYWORDS = {
    TASK_INTENT_READ: [
        "读取",
        "查看",
        "看一下",
        "打开",
        "告诉我内容",
        "内容",
        "read",
        "show",
        "open",
    ],
    TASK_INTENT_SEARCH: [
        "搜索",
        "查找",
        "寻找",
        "定位",
        "在哪",
        "哪里",
        "find",
        "search",
        "locate",
    ],
    TASK_INTENT_ANALYZE: [
        "分析",
        "解释",
        "总结",
        "梳理",
        "潜在问题",
        "问题",
        "结构",
        "接口",
        "函数",
        "review",
        "analyze",
        "explain",
    ],
    TASK_INTENT_EDIT: [
        "修改",
        "修复",
        "改成",
        "新增",
        "创建",
        "写入",
        "删除",
        "实现",
        "补充",
        "重构",
        "fix",
        "edit",
        "modify",
        "create",
        "write",
        "delete",
        "implement",
    ],
    TASK_INTENT_TEST: [
        "测试",
        "运行",
        "检查",
        "pytest",
        "py_compile",
        "单元测试",
        "test",
        "run",
        "check",
    ],
    TASK_INTENT_GIT: [
        "git",
        "status",
        "diff",
        "提交",
        "commit",
        "分支",
        "branch",
        "tag",
    ],
    TASK_INTENT_PLAN: [
        "计划",
        "规划",
        "拆分",
        "步骤",
        "方案",
        "plan",
        "steps",
    ],
}


def normalize_task_text(text: str | None) -> str:
    """
    统一清理用户任务文本。

    这个函数的作用：
    - 处理 None
    - 去掉首尾空格
    - 把连续空白压缩成一个空格
    """
    if not text:
        return ""

    return re.sub(r"\s+", " ", text).strip()


def contains_any_keyword(text: str, keywords: list[str]) -> bool:
    """
    判断文本中是否包含任意关键词。
    """
    lower_text = text.lower()

    for keyword in keywords:
        if keyword.lower() in lower_text:
            return True

    return False


def detect_task_intents(user_message: str) -> list[str]:
    """
    根据用户输入判断任务意图。

    一个任务可以有多个意图。

    例如：
    “请修改 main.py 并运行测试”
    会识别出：
    - edit
    - test
    """
    text = normalize_task_text(user_message)

    if not text:
        return [TASK_INTENT_ANALYZE]

    intents: list[str] = []

    for intent, keywords in INTENT_KEYWORDS.items():
        if contains_any_keyword(text, keywords):
            intents.append(intent)

    if not intents:
        intents.append(TASK_INTENT_ANALYZE)

    return deduplicate_keep_order(intents)


def deduplicate_keep_order(items: list[str]) -> list[str]:
    """
    去重，但保持原顺序。

    为什么不用 set？
    因为 set 会打乱顺序。
    任务规划里顺序很重要。
    """
    seen = set()
    result = []

    for item in items:
        if item in seen:
            continue

        seen.add(item)
        result.append(item)

    return result


def extract_target_paths(user_message: str) -> list[str]:
    """
    从用户任务中提取可能的文件或目录路径。

    支持几类常见写法：
    - demo_project/main.py
    - `demo_project/main.py`
    - app/agent/agent_loop.py
    - tests/test_xxx.py
    """
    text = normalize_task_text(user_message)

    if not text:
        return []

    path_pattern = r"`?([A-Za-z0-9_\-./\\]+(?:\.py|\.md|\.json|\.txt|\.yaml|\.yml|/|\\)[A-Za-z0-9_\-./\\]*)`?"

    matches = re.findall(path_pattern, text)

    cleaned_paths = []

    for path in matches:
        cleaned = path.strip("`").strip()

        if not cleaned:
            continue

        if cleaned in {".", "./"}:
            continue

        cleaned_paths.append(cleaned)

    return deduplicate_keep_order(cleaned_paths)


def estimate_task_complexity(intents: list[str], target_paths: list[str], user_message: str) -> str:
    """
    粗略估计任务复杂度。

    simple：
    - 单纯读取一个文件
    - 简单查看内容

    medium：
    - 搜索、分析、测试
    - 多个文件

    complex：
    - 修改代码
    - 同时包含 edit + test
    - 用户要求完整分析、所有文件、潜在问题
    """
    text = normalize_task_text(user_message)

    if TASK_INTENT_EDIT in intents:
        return TASK_COMPLEXITY_COMPLEX

    if TASK_INTENT_TEST in intents and len(intents) >= 2:
        return TASK_COMPLEXITY_COMPLEX

    if "所有文件" in text or "完整分析" in text or "潜在问题" in text:
        return TASK_COMPLEXITY_COMPLEX

    if len(target_paths) >= 2:
        return TASK_COMPLEXITY_MEDIUM

    if TASK_INTENT_ANALYZE in intents or TASK_INTENT_SEARCH in intents or TASK_INTENT_TEST in intents:
        return TASK_COMPLEXITY_MEDIUM

    return TASK_COMPLEXITY_SIMPLE


def estimate_tools_for_intents(intents: list[str]) -> list[str]:
    """
    根据任务意图推荐可能需要的工具。

    注意：
    这里不是实际执行工具，只是规划。
    """
    tools: list[str] = []

    if TASK_INTENT_PLAN in intents:
        tools.extend([
            "list_files",
        ])

    if TASK_INTENT_READ in intents:
        tools.extend([
            "list_files",
            "read_file",
        ])

    if TASK_INTENT_SEARCH in intents:
        tools.extend([
            "list_files",
            "search_code",
        ])

    if TASK_INTENT_ANALYZE in intents:
        tools.extend([
            "list_files",
            "read_file",
            "search_code",
        ])

    if TASK_INTENT_EDIT in intents:
        tools.extend([
            "read_file",
            "edit_file",
            "get_workspace_diff",
        ])

    if TASK_INTENT_TEST in intents:
        tools.extend([
            "run_command",
        ])

    if TASK_INTENT_GIT in intents:
        tools.extend([
            "get_git_status",
            "get_git_diff",
        ])

    if not tools:
        tools.extend([
            "list_files",
            "read_file",
        ])

    return deduplicate_keep_order(tools)


def estimate_plan_risk(tools: list[str]) -> str:
    """
    根据推荐工具估计任务风险。

    高风险：
    - 需要 edit_file / write_new_file / ensure_gitignore 等写操作

    中风险：
    - 需要 run_command

    低风险：
    - 只读工具
    """
    risk_levels = [get_tool_risk_level(tool) for tool in tools]

    if TASK_RISK_HIGH in risk_levels:
        return TASK_RISK_HIGH

    if TASK_RISK_MEDIUM in risk_levels:
        return TASK_RISK_MEDIUM

    return TASK_RISK_LOW


def build_plan_step(
    *,
    index: int,
    title: str,
    description: str,
    suggested_tool: str | None,
    reason: str,
) -> dict[str, Any]:
    """
    构建单个计划步骤。

    每个步骤都包含：
    - 编号
    - 标题
    - 描述
    - 推荐工具
    - 风险等级
    - 原因
    """
    risk_level = get_tool_risk_level(suggested_tool) if suggested_tool else TASK_RISK_LOW

    return {
        "index": index,
        "title": title,
        "description": description,
        "suggested_tool": suggested_tool,
        "risk_level": risk_level,
        "reason": reason,
    }


def build_steps_for_plan(
    intents: list[str],
    target_paths: list[str],
    complexity: str,
) -> list[dict[str, Any]]:
    """
    根据任务意图生成执行步骤。

    这是 Task Planner 的核心函数。
    """
    steps: list[dict[str, Any]] = []
    index = 1

    has_target = bool(target_paths)
    target_text = "、".join(target_paths) if has_target else "目标工作区"

    if TASK_INTENT_PLAN in intents or TASK_INTENT_ANALYZE in intents or not has_target:
        steps.append(build_plan_step(
            index=index,
            title="查看项目结构",
            description=f"先查看 {target_text} 的目录结构，确认需要处理的文件范围。",
            suggested_tool="list_files",
            reason="在读取或修改文件前，需要先理解项目结构。",
        ))
        index += 1

    if TASK_INTENT_SEARCH in intents:
        steps.append(build_plan_step(
            index=index,
            title="搜索相关代码",
            description="根据用户任务中的关键词搜索相关代码位置。",
            suggested_tool="search_code",
            reason="搜索可以快速定位相关函数、接口或配置。",
        ))
        index += 1

    if (TASK_INTENT_READ in intents or TASK_INTENT_ANALYZE in intents) and TASK_INTENT_EDIT not in intents:
        steps.append(build_plan_step(
            index=index,
            title="读取关键文件",
            description=f"读取与任务相关的关键文件：{target_text}。",
            suggested_tool="read_file",
            reason="读取文件内容后才能进行准确分析。",
        ))
        index += 1

    if TASK_INTENT_EDIT in intents:
        steps.append(build_plan_step(
            index=index,
            title="读取修改目标",
            description=f"在修改前读取目标文件，确认当前实现和修改位置：{target_text}。",
            suggested_tool="read_file",
            reason="修改前必须先读取原始内容，避免盲改。",
        ))
        index += 1

        steps.append(build_plan_step(
            index=index,
            title="执行代码修改",
            description="根据任务要求修改文件内容。",
            suggested_tool="edit_file",
            reason="用户任务包含修改、修复、新增或实现需求。",
        ))
        index += 1

        steps.append(build_plan_step(
            index=index,
            title="查看修改差异",
            description="修改后查看 workspace diff，确认变更范围是否符合预期。",
            suggested_tool="get_workspace_diff",
            reason="修改后必须检查 diff，避免误改其他文件。",
        ))
        index += 1

    if TASK_INTENT_TEST in intents or TASK_INTENT_EDIT in intents:
        steps.append(build_plan_step(
            index=index,
            title="运行验证命令",
            description="运行合适的测试或语法检查命令，验证修改是否正确。",
            suggested_tool="run_command",
            reason="执行测试可以确认代码没有引入明显错误。",
        ))
        index += 1

    if TASK_INTENT_GIT in intents:
        steps.append(build_plan_step(
            index=index,
            title="查看 Git 状态",
            description="查看当前 Git 工作区状态。",
            suggested_tool="get_git_status",
            reason="Git 状态可以帮助确认哪些文件发生了变化。",
        ))
        index += 1

        steps.append(build_plan_step(
            index=index,
            title="查看 Git 差异",
            description="查看 Git diff，确认代码变更内容。",
            suggested_tool="get_git_diff",
            reason="提交或总结前应查看完整差异。",
        ))
        index += 1

    if complexity == TASK_COMPLEXITY_COMPLEX:
        steps.append(build_plan_step(
            index=index,
            title="汇总结果与风险",
            description="总结执行结果、关键发现、潜在问题和后续建议。",
            suggested_tool=None,
            reason="复杂任务需要最终汇总，方便用户理解执行结果。",
        ))

    return steps


def build_task_warnings(
    intents: list[str],
    risk_level: str,
    complexity: str,
) -> list[str]:
    """
    根据任务风险和复杂度生成提醒。
    """
    warnings: list[str] = []

    if risk_level == TASK_RISK_HIGH:
        warnings.append("该任务可能涉及写文件操作，需要用户审批后才能执行高风险工具。")

    if risk_level == TASK_RISK_MEDIUM:
        warnings.append("该任务可能涉及命令执行，需要注意命令安全和执行目录。")

    if complexity == TASK_COMPLEXITY_COMPLEX:
        warnings.append("该任务较复杂，建议分步骤执行，并在关键节点检查结果。")

    if TASK_INTENT_EDIT in intents and TASK_INTENT_TEST not in intents:
        warnings.append("任务包含代码修改，但用户未明确要求测试；建议修改后补充验证步骤。")

    return warnings


def build_task_plan(user_message: str) -> dict[str, Any]:
    """
    构建任务规划结果。

    这是对外使用的主入口函数。

    输入：
    - 用户自然语言任务

    输出：
    - 结构化任务计划
    """
    normalized_message = normalize_task_text(user_message)
    intents = detect_task_intents(normalized_message)
    target_paths = extract_target_paths(normalized_message)
    tools = estimate_tools_for_intents(intents)
    risk_level = estimate_plan_risk(tools)
    complexity = estimate_task_complexity(
        intents=intents,
        target_paths=target_paths,
        user_message=normalized_message,
    )
    steps = build_steps_for_plan(
        intents=intents,
        target_paths=target_paths,
        complexity=complexity,
    )
    warnings = build_task_warnings(
        intents=intents,
        risk_level=risk_level,
        complexity=complexity,
    )

    return {
        "objective": normalized_message,
        "intents": intents,
        "target_paths": target_paths,
        "suggested_tools": tools,
        "risk_level": risk_level,
        "complexity": complexity,
        "needs_approval": risk_level == TASK_RISK_HIGH,
        "estimated_steps": len(steps),
        "steps": steps,
        "warnings": warnings,
    }