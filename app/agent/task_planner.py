import re
from typing import Any

from app.agent.tool_policy import (
    get_tool_risk_level,
)


# ============================================================
# 任务意图
# ============================================================

TASK_INTENT_READ = "read"
TASK_INTENT_SEARCH = "search"
TASK_INTENT_ANALYZE = "analyze"
TASK_INTENT_EDIT = "edit"
TASK_INTENT_TEST = "test"
TASK_INTENT_GIT = "git"
TASK_INTENT_PLAN = "plan"


# ============================================================
# 任务复杂度
# ============================================================

TASK_COMPLEXITY_SIMPLE = "simple"
TASK_COMPLEXITY_MEDIUM = "medium"
TASK_COMPLEXITY_COMPLEX = "complex"


# ============================================================
# 任务风险
# ============================================================

TASK_RISK_LOW = "low"
TASK_RISK_MEDIUM = "medium"
TASK_RISK_HIGH = "high"

# ============================================================
# Planner 元数据
# ============================================================

PLANNER_VERSION = "2.1"

PLAN_CONFIDENCE_LOW = "low"
PLAN_CONFIDENCE_MEDIUM = "medium"
PLAN_CONFIDENCE_HIGH = "high"


# ============================================================
# 基础关键词
#
# edit 和 test 仍然保留在这里，
# 但是 Planner v2 不会再仅凭一个关键词
# 就直接认定为编辑或测试任务。
# ============================================================

INTENT_KEYWORDS = {
    TASK_INTENT_READ: [
        "读取",
        "查看",
        "看一下",
        "打开",
        "告诉我内容",
        "读取代码",
        "查看代码",
        "具体代码",
        "具体实现",
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
        "定义位置",
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
        "依赖",
        "影响",
        "风险",
        "受影响",
        "兼容性",
        "调用关系",
        "检查清单",
        "注意事项",
        "review",
        "analyze",
        "explain",
        "impact",
        "dependency",
    ],
    TASK_INTENT_EDIT: [
        "修改",
        "修复",
        "改成",
        "改为",
        "新增",
        "添加",
        "增加",
        "插入",
        "追加",
        "加入",
        "加上",
        "补上",
        "创建",
        "新建",
        "写入",
        "删除",
        "移除",
        "删掉",
        "实现",
        "补充",
        "重构",
        "替换",
        "fix",
        "edit",
        "modify",
        "create",
        "write",
        "delete",
        "implement",
        "refactor",
        "replace",
    ],
    TASK_INTENT_TEST: [
        "测试",
        "运行",
        "执行",
        "验证",
        "检查",
        "pytest",
        "py_compile",
        "单元测试",
        "集成测试",
        "test",
        "run",
        "check",
        "verify",
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


# ============================================================
# “分析修改影响”模式
#
# 匹配的是：
# - 如果修改会影响什么
# - 分析修改风险
# - 修改后应该检查什么
#
# 这些场景只是在讨论修改，
# 不表示要求 Agent 现在写文件。
# ============================================================

ANALYSIS_ONLY_EDIT_PATTERNS = [
    (
        r"(如果|假如|假设|若|当)"
        r".{0,25}"
        r"(修改|改动|重构|删除|新增|替换|"
        r"添加|增加|插入|追加|加入|移除|删掉)"
        r".{0,40}"
        r"(影响|风险|注意|检查|会怎样|后果|问题)"
    ),
    (
        r"(分析|评估|查看|说明|告诉我|列出)"
        r".{0,35}"
        r"(修改|改动|重构|删除|新增|替换|"
        r"添加|增加|插入|追加|加入|移除|删掉)"
        r".{0,35}"
        r"(影响|风险|注意|检查|范围|后果|问题)"
    ),
    (
        r"(修改|改动|重构|删除|新增|替换|"
        r"添加|增加|插入|追加|加入|移除|删掉)"
        r".{0,25}"
        r"(前|后)"
        r".{0,25}"
        r"(应该|需要|重点|要)"
        r".{0,12}"
        r"(检查|注意|验证|测试)"
    ),
    (
        r"(修改|改动|重构|替换|"
        r"添加|增加|插入|追加|加入|移除|删掉)"
        r".{0,25}"
        r"(会|可能)"
        r".{0,12}"
        r"(影响|导致|引发)"
    ),
    (
        r"(修改|改动|重构|添加|增加|插入|追加|加入)"
        r".{0,25}"
        r"(影响范围|受影响文件|依赖方)"
    ),
]


# ============================================================
# 明确的写操作请求
#
# 只有出现明确命令形式时，
# 才把任务判断为 edit。
# ============================================================

EXPLICIT_EDIT_PATTERNS = [
    (
        r"(请|帮我|麻烦|直接|现在|立即|需要你)"
        r".{0,10}"
        r"(修改|修复|新增|添加|增加|插入|追加|加入|"
        r"加上|补上|创建|新建|写入|删除|移除|删掉|"
        r"实现|补充|重构|替换)"
    ),
    (
        r"^"
        r"(请|帮我|麻烦|直接|现在|立即|需要你)?"
        r"\s*"
        r"(在|向|给)"
        r".{1,120}"
        r"(添加|新增|增加|插入|追加|加入|"
        r"加上|补上|写入|删除|移除)"
    ),
    (
        # 支持“请在某个文件/类/函数的某个位置添加内容”。
        #
        # 示例：
        # 请在 demo_project/models.py 的 User 类上方添加注释
        # 帮我给 User 类增加 email 字段
        r"(请|帮我|麻烦|直接|现在|立即|需要你)"
        r".{0,15}"
        r"(在|向|给)"
        r".{0,100}"
        r"(添加|增加|插入|追加|加入|加上|补上|"
        r"移除|删掉|删除)"
    ),
    (
        r"(把|将)"
        r".{0,60}"
        r"(改成|改为|替换为|删除|移除|删掉|新增|"
        r"添加|增加|插入|追加|加入|加上|补上)"
    ),
    (
        r"(同步修改|直接修改|执行修改|"
        r"开始修改|立即修改|直接添加|立即添加)"
    ),
    (
        r"^(修改|修复|新增|添加|增加|插入|追加|加入|"
        r"加上|补上|创建|新建|删除|移除|删掉|"
        r"实现|补充|重构|替换)"
    ),
    (
        # 支持省略“请”的命令式位置表达。
        #
        # 示例：
        # 在 User 类上方添加注释
        # 给 User 类增加字段
        r"^(在|向|给)"
        r".{0,100}"
        r"(添加|增加|插入|追加|加入|加上|补上|"
        r"移除|删掉|删除)"
    ),
    (
        r"\b("
        r"fix|edit|modify|create|write|delete|"
        r"implement|refactor|replace|add|insert|append|remove"
        r")\b"
    ),
]


# ============================================================
# “告诉我检查什么”模式
#
# 这些是分析建议，不是运行测试。
# ============================================================

ADVISORY_CHECK_PATTERNS = [
    (
        r"(告诉我|说明|列出|分析|总结)"
        r".{0,25}"
        r"(应该|需要|重点|要)"
        r".{0,12}"
        r"(检查|验证|测试|注意)"
    ),
    (
        r"(检查|验证|测试)"
        r"(清单|要点|重点|哪些|什么|建议)"
    ),
    (
        r"(修改|改动|重构)"
        r".{0,25}"
        r"(后|前)"
        r".{0,25}"
        r"(应该|需要|重点|要)"
        r".{0,12}"
        r"(检查|验证|测试|注意)"
    ),
    (
        r"(需要注意什么|应该注意什么|"
        r"重点看什么|重点检查什么)"
    ),
]


# ============================================================
# 明确的测试或命令执行请求
# ============================================================

EXPLICIT_TEST_PATTERNS = [
    (
        r"(请|帮我|麻烦|直接|现在|然后|并且)"
        r".{0,8}"
        r"(运行|执行|跑)"
        r".{0,25}"
        r"(pytest|py_compile|测试|单元测试|"
        r"集成测试|命令|项目|脚本|程序)"
    ),
    (
        r"(运行|执行|跑)"
        r".{0,25}"
        r"(pytest|py_compile|测试|单元测试|"
        r"集成测试|检查命令)"
    ),
    (
        r"(帮我测试|帮我验证|进行测试|"
        r"执行测试|运行测试)"
    ),
    (
        r"(验证|测试)"
        r".{0,25}"
        r"(代码|接口|功能|修改|结果)"
        r".{0,15}"
        r"(是否|能否|有没有|通过|正确|可用)"
    ),
    (
        r"^(测试|验证|运行|执行|跑)"
    ),
    (
        r"\b(pytest|py_compile)\b"
    ),
]


# ============================================================
# 特殊代码理解任务模式
# ============================================================

PYTHON_SYMBOL_PATTERNS = [
    (
        r"(查找|搜索|寻找|定位|在哪|哪里)"
        r".{0,30}"
        r"(类|函数|方法|符号)"
        r".{0,15}"
        r"(定义|位置|实现)?"
    ),
    (
        r"(类|函数|方法|符号)"
        r".{0,25}"
        r"(定义在哪|在哪里定义|定义位置)"
    ),
    (
        r"(class|function|method|symbol)"
        r".{0,25}"
        r"(definition|locate|find|search)"
    ),
]


PYTHON_OUTLINE_PATTERNS = [
    r"(Python|python).{0,10}(文件|代码).{0,10}(结构|大纲)",
    r"(代码结构|文件结构|代码大纲|文件大纲)",
    r"(有哪些|包含哪些).{0,20}(类|函数|方法|import|导入)",
    r"(类、函数和方法|类和函数|函数和方法)",
    r"\boutline\b",
]


PYTHON_DEPENDENCY_PATTERNS = [
    r"(依赖了哪些|依赖哪些|依赖谁)",
    r"(导入了哪些|导入哪些).{0,15}(本地|项目|模块|文件)?",
    r"(正向依赖|本地依赖|import 依赖|import依赖)",
    r"(当前文件|这个文件).{0,20}(依赖|导入).{0,20}(谁|哪些)",
]


PYTHON_IMPACT_PATTERNS = [
    r"(影响范围|受影响范围|受影响文件)",
    r"(谁依赖|哪些文件.{0,15}依赖)",
    r"(直接依赖|间接依赖|反向依赖|依赖方)",
    (
        r"(修改|改动|重构|删除|替换)"
        r".{0,30}"
        r"(影响|受影响|风险)"
    ),
    (
        r"(影响|受影响)"
        r".{0,20}"
        r"(哪些文件|什么文件|模块)"
    ),
]


NEW_FILE_PATTERNS = [
    # 直接识别“创建/新建/生成 + 文件路径”。
    #
    # 支持：
    # 请创建 demo_project/config.json 文件
    # 新建 app/services/user_service.py
    # 生成 README.md
    (
        r"(请|帮我|麻烦|直接)?"
        r"\s*"
        r"(创建|新建|生成)"
        r"\s+"
        r"[`\"']?"
        r"[A-Za-z0-9_\-./\\]+"
        r"\."
        r"(py|md|json|txt|yaml|yml)"
        r"[`\"']?"
        r"\s*"
        r"(文件)?"
    ),

    # 兼容没有明确扩展名的自然语言表达。
    #
    # 原来是 {0,25}，路径稍长就会匹配失败。
    # 增大为 {0,80}，但仍然限制最大距离，
    # 避免规则匹配到完全无关的长句。
    (
        r"(创建|新建|生成)"
        r".{0,80}"
        r"(文件|README|配置)"
    ),

    # 例如：
    # 新增一个配置文件
    (
        r"(新增)"
        r".{0,40}"
        r"(文件)"
    ),

    # 英文：
    # create config.json file
    (
        r"\b(create|generate)"
        r".{0,60}"
        r"(file)\b"
    ),
]


def normalize_task_text(
    text: str | None,
) -> str:
    """
    统一清理用户任务文本。

    作用：
    - 处理 None；
    - 去掉首尾空格；
    - 把连续空白压缩成一个空格。
    """

    if not text:
        return ""

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def contains_any_keyword(
    text: str,
    keywords: list[str],
) -> bool:
    """
    判断文本中是否包含任意关键词。
    """

    lower_text = text.lower()

    for keyword in keywords:
        if keyword.lower() in lower_text:
            return True

    return False


def matches_any_pattern(
    text: str,
    patterns: list[str],
) -> bool:
    """
    判断文本是否匹配任意正则表达式。

    re.IGNORECASE：
    英文匹配时不区分大小写。
    """

    for pattern in patterns:
        if re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        ):
            return True

    return False


def find_matching_keywords(
    text: str,
    keywords: list[str],
) -> list[str]:
    """
    返回文本中实际命中的关键词。

    例如：

        text = "请读取并分析 main.py"
        keywords = ["读取", "查看", "分析"]

    返回：

        ["读取", "分析"]

    与 contains_any_keyword 不同：

    contains_any_keyword
    只回答有没有命中。

    find_matching_keywords
    会告诉我们具体命中了哪些词。
    """

    lower_text = text.lower()

    matched: list[str] = []

    for keyword in keywords:
        if keyword.lower() in lower_text:
            matched.append(keyword)

    return deduplicate_keep_order(
        matched
    )


def find_matching_pattern_indexes(
    text: str,
    patterns: list[str],
) -> list[int]:
    """
    返回命中的正则规则编号。

    例如第 1 条和第 3 条规则命中：

        [1, 3]

    不直接把完整正则写入 task_plan，
    是因为正则表达式很长，
    会让 Trace 难以阅读。
    """

    matched_indexes: list[int] = []

    for index, pattern in enumerate(
        patterns,
        start=1,
    ):
        if re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        ):
            matched_indexes.append(index)

    return matched_indexes


def is_analysis_only_edit_context(
    text: str,
) -> bool:
    """
    判断文本中的“修改”是否只是分析语境。

    例如：
    - 如果修改会影响什么？
    - 修改后应该检查什么？
    """

    return matches_any_pattern(
        text,
        ANALYSIS_ONLY_EDIT_PATTERNS,
    )


def is_explicit_edit_request(
    text: str,
) -> bool:
    """
    判断用户是否明确要求执行写操作。

    先识别明确命令；
    如果只是影响分析，则不算 edit。
    """

    if not contains_any_keyword(
        text,
        INTENT_KEYWORDS[TASK_INTENT_EDIT],
    ):
        return False

    explicit_request = matches_any_pattern(
        text,
        EXPLICIT_EDIT_PATTERNS,
    )

    if explicit_request:
        return True

    if is_analysis_only_edit_context(text):
        return False

    return False


def is_advisory_check_context(
    text: str,
) -> bool:
    """
    判断“检查”是否只是建议或分析语境。

    例如：
    - 告诉我应该检查什么；
    - 列出检查清单。
    """

    return matches_any_pattern(
        text,
        ADVISORY_CHECK_PATTERNS,
    )


def is_explicit_test_request(
    text: str,
) -> bool:
    """
    判断用户是否明确要求运行测试或命令。
    """

    explicit_request = matches_any_pattern(
        text,
        EXPLICIT_TEST_PATTERNS,
    )

    if explicit_request:
        return True

    if is_advisory_check_context(text):
        return False

    return False


def detect_task_context(
    user_message: str,
) -> dict[str, bool]:
    """
    识别任务中的特殊代码理解场景。

    这些场景会影响 Planner 推荐什么工具。
    """

    text = normalize_task_text(
        user_message
    )

    return {
        "python_symbol_search": (
            matches_any_pattern(
                text,
                PYTHON_SYMBOL_PATTERNS,
            )
        ),
        "python_outline": (
            matches_any_pattern(
                text,
                PYTHON_OUTLINE_PATTERNS,
            )
        ),
        "python_dependencies": (
            matches_any_pattern(
                text,
                PYTHON_DEPENDENCY_PATTERNS,
            )
        ),
        "python_impact": (
            matches_any_pattern(
                text,
                PYTHON_IMPACT_PATTERNS,
            )
        ),
        "new_file_request": (
            matches_any_pattern(
                text,
                NEW_FILE_PATTERNS,
            )
        ),
        "analysis_only_edit": (
            is_analysis_only_edit_context(
                text
            )
        ),
        "advisory_check": (
            is_advisory_check_context(
                text
            )
        ),
    }


def build_intent_evidence(
    *,
    user_message: str,
    intents: list[str],
    context: dict[str, bool],
) -> dict[str, list[dict[str, Any]]]:
    """
    为每个被识别出的意图生成判断证据。

    返回示例：

        {
            "read": [
                {
                    "type": "keyword",
                    "value": "读取",
                    "reason": "命中了读取意图关键词。"
                }
            ]
        }
    """

    text = normalize_task_text(
        user_message
    )

    evidence: dict[
        str,
        list[dict[str, Any]],
    ] = {}

    def add_evidence(
        *,
        intent: str,
        evidence_type: str,
        value: Any,
        reason: str,
    ) -> None:
        """
        给某个意图增加一条证据。
        """

        evidence.setdefault(
            intent,
            [],
        ).append(
            {
                "type": evidence_type,
                "value": value,
                "reason": reason,
            }
        )

    # --------------------------------------------------------
    # 基础关键词证据
    # --------------------------------------------------------

    for intent in [
        TASK_INTENT_READ,
        TASK_INTENT_SEARCH,
        TASK_INTENT_ANALYZE,
        TASK_INTENT_GIT,
        TASK_INTENT_PLAN,
    ]:
        if intent not in intents:
            continue

        matched_keywords = (
            find_matching_keywords(
                text,
                INTENT_KEYWORDS[intent],
            )
        )

        for keyword in matched_keywords:
            add_evidence(
                intent=intent,
                evidence_type="keyword",
                value=keyword,
                reason=(
                    f"命中了 {intent} "
                    "意图关键词。"
                ),
            )

    # --------------------------------------------------------
    # 明确编辑证据
    # --------------------------------------------------------

    if TASK_INTENT_EDIT in intents:
        pattern_indexes = (
            find_matching_pattern_indexes(
                text,
                EXPLICIT_EDIT_PATTERNS,
            )
        )

        add_evidence(
            intent=TASK_INTENT_EDIT,
            evidence_type=(
                "explicit_pattern"
            ),
            value=pattern_indexes,
            reason=(
                "识别到明确的写操作命令，"
                "例如“请修改”或"
                "“把 A 改成 B”。"
            ),
        )

    # --------------------------------------------------------
    # 明确测试证据
    # --------------------------------------------------------

    if TASK_INTENT_TEST in intents:
        pattern_indexes = (
            find_matching_pattern_indexes(
                text,
                EXPLICIT_TEST_PATTERNS,
            )
        )

        add_evidence(
            intent=TASK_INTENT_TEST,
            evidence_type=(
                "explicit_pattern"
            ),
            value=pattern_indexes,
            reason=(
                "识别到明确的测试或"
                "命令执行请求。"
            ),
        )

    # --------------------------------------------------------
    # 特殊代码理解上下文证据
    # --------------------------------------------------------

    if context["python_symbol_search"]:
        add_evidence(
            intent=TASK_INTENT_SEARCH,
            evidence_type="context",
            value="python_symbol_search",
            reason=(
                "识别到 Python 类、函数"
                "或方法定义搜索场景。"
            ),
        )

    if context["python_outline"]:
        add_evidence(
            intent=TASK_INTENT_ANALYZE,
            evidence_type="context",
            value="python_outline",
            reason=(
                "识别到 Python 文件"
                "结构分析场景。"
            ),
        )

    if context["python_dependencies"]:
        add_evidence(
            intent=TASK_INTENT_ANALYZE,
            evidence_type="context",
            value="python_dependencies",
            reason=(
                "识别到 Python 正向"
                "依赖分析场景。"
            ),
        )

    if context["python_impact"]:
        add_evidence(
            intent=TASK_INTENT_ANALYZE,
            evidence_type="context",
            value="python_impact",
            reason=(
                "识别到 Python 反向依赖"
                "与影响范围分析场景。"
            ),
        )

    if context["new_file_request"]:
        add_evidence(
            intent=TASK_INTENT_EDIT,
            evidence_type="context",
            value="new_file_request",
            reason=(
                "识别到明确的新建文件请求。"
            ),
        )

    return evidence


def build_suppressed_intents(
    *,
    user_message: str,
    intents: list[str],
    context: dict[str, bool],
) -> list[dict[str, Any]]:
    """
    记录文本中出现了关键词，
    但被上下文规则主动排除的高风险意图。

    主要处理：

    1. 提到“修改”，但只是分析影响；
    2. 提到“检查”，但只是询问检查建议。
    """

    text = normalize_task_text(
        user_message
    )

    suppressed: list[
        dict[str, Any]
    ] = []

    edit_keywords = (
        find_matching_keywords(
            text,
            INTENT_KEYWORDS[
                TASK_INTENT_EDIT
            ],
        )
    )

    if (
        edit_keywords
        and TASK_INTENT_EDIT
        not in intents
    ):
        if context["analysis_only_edit"]:
            reason = (
                "编辑关键词出现在修改影响、"
                "风险或注意事项分析语境中，"
                "不表示要求立即执行写操作。"
            )
        else:
            reason = (
                "虽然出现编辑关键词，"
                "但没有匹配到明确的写操作"
                "命令形式。"
            )

        suppressed.append(
            {
                "intent": TASK_INTENT_EDIT,
                "matched_keywords": (
                    edit_keywords
                ),
                "reason": reason,
            }
        )

    test_keywords = (
        find_matching_keywords(
            text,
            INTENT_KEYWORDS[
                TASK_INTENT_TEST
            ],
        )
    )

    if (
        test_keywords
        and TASK_INTENT_TEST
        not in intents
    ):
        if context["advisory_check"]:
            reason = (
                "测试或检查关键词用于请求"
                "检查建议、清单或注意事项，"
                "不表示要求运行测试命令。"
            )
        else:
            reason = (
                "虽然出现测试相关关键词，"
                "但没有匹配到明确的测试或"
                "命令执行请求。"
            )

        suppressed.append(
            {
                "intent": TASK_INTENT_TEST,
                "matched_keywords": (
                    test_keywords
                ),
                "reason": reason,
            }
        )

    return suppressed


def estimate_planner_confidence(
    *,
    user_message: str,
    intents: list[str],
    context: dict[str, bool],
    suppressed_intents: list[
        dict[str, Any]
    ],
) -> dict[str, Any]:
    """
    估计 Planner 对本次判断的置信度。

    这是确定性规则评分，
    不是机器学习模型概率。
    """

    text = normalize_task_text(
        user_message
    )

    score = 0.55
    reasons: list[str] = []

    special_context_flags = [
        name
        for name in [
            "python_symbol_search",
            "python_outline",
            "python_dependencies",
            "python_impact",
            "new_file_request",
        ]
        if context[name]
    ]

    # 命中特定代码任务场景时，
    # 比普通关键词分析更可靠。
    if special_context_flags:
        score += 0.20

        reasons.append(
            "命中了明确的代码理解"
            "或文件操作场景。"
        )

    # 明确写操作模式提高确定性。
    if TASK_INTENT_EDIT in intents:
        if is_explicit_edit_request(text):
            score += 0.15

            reasons.append(
                "写操作意图由明确命令"
                "模式确认。"
            )
        else:
            score -= 0.15

            reasons.append(
                "存在编辑意图，但没有"
                "明确写操作模式支持。"
            )

    # 明确测试模式提高确定性。
    if TASK_INTENT_TEST in intents:
        if is_explicit_test_request(text):
            score += 0.15

            reasons.append(
                "测试意图由明确命令"
                "模式确认。"
            )
        else:
            score -= 0.15

            reasons.append(
                "存在测试意图，但没有"
                "明确执行模式支持。"
            )

    # 成功识别并抑制歧义高风险意图，
    # 说明上下文规则提供了额外证据。
    if suppressed_intents:
        score += 0.10

        reasons.append(
            "上下文规则成功排除了"
            "歧义的高风险意图。"
        )

    # 意图过多往往说明文本复杂，
    # 规则判断的不确定性更高。
    if len(intents) >= 5:
        score -= 0.10

        reasons.append(
            "任务同时包含较多意图，"
            "自然语言歧义可能增加。"
        )

    # 没有命中任何明显关键词或特殊上下文，
    # 只能使用 analyze 兜底。
    all_basic_keywords: list[str] = []

    for keywords in INTENT_KEYWORDS.values():
        all_basic_keywords.extend(keywords)

    matched_basic_keywords = (
        find_matching_keywords(
            text,
            all_basic_keywords,
        )
    )

    if (
        intents == [TASK_INTENT_ANALYZE]
        and not matched_basic_keywords
        and not special_context_flags
    ):
        score = min(score, 0.45)

        reasons.append(
            "没有命中明确关键词，"
            "当前使用 analyze 默认兜底。"
        )

    # 限制在 0～1 范围内。
    score = max(
        0.0,
        min(1.0, score),
    )

    score = round(score, 2)

    if score >= 0.80:
        level = PLAN_CONFIDENCE_HIGH
    elif score >= 0.60:
        level = PLAN_CONFIDENCE_MEDIUM
    else:
        level = PLAN_CONFIDENCE_LOW

    return {
        "score": score,
        "level": level,
        "reasons": reasons,
    }


def build_decision_summary(
    *,
    intents: list[str],
    tools: list[str],
    risk_level: str,
    suppressed_intents: list[
        dict[str, Any]
    ],
) -> str:
    """
    生成一段简短、可读的规划决策摘要。
    """

    intent_text = (
        "、".join(intents)
        if intents
        else "无"
    )

    tool_text = (
        "、".join(tools)
        if tools
        else "无"
    )

    summary = (
        f"识别意图：{intent_text}；"
        f"推荐工具：{tool_text}；"
        f"计划风险：{risk_level}。"
    )

    if suppressed_intents:
        suppressed_text = "、".join(
            item["intent"]
            for item
            in suppressed_intents
        )

        summary += (
            f" 已根据上下文抑制："
            f"{suppressed_text}。"
        )

    return summary


def build_planner_meta(
    *,
    user_message: str,
    intents: list[str],
    tools: list[str],
    risk_level: str,
) -> dict[str, Any]:
    """
    构建 Planner v2.1 可解释元数据。
    """

    context = detect_task_context(
        user_message
    )

    intent_evidence = (
        build_intent_evidence(
            user_message=user_message,
            intents=intents,
            context=context,
        )
    )

    suppressed_intents = (
        build_suppressed_intents(
            user_message=user_message,
            intents=intents,
            context=context,
        )
    )

    confidence = (
        estimate_planner_confidence(
            user_message=user_message,
            intents=intents,
            context=context,
            suppressed_intents=(
                suppressed_intents
            ),
        )
    )

    active_context_flags = [
        name
        for name, enabled
        in context.items()
        if enabled
    ]

    decision_summary = (
        build_decision_summary(
            intents=intents,
            tools=tools,
            risk_level=risk_level,
            suppressed_intents=(
                suppressed_intents
            ),
        )
    )

    return {
        "version": PLANNER_VERSION,
        "confidence": confidence,
        "intent_evidence": (
            intent_evidence
        ),
        "suppressed_intents": (
            suppressed_intents
        ),
        "context_flags": (
            active_context_flags
        ),
        "decision_summary": (
            decision_summary
        ),
    }


def detect_task_intents(
    user_message: str,
) -> list[str]:
    """
    根据用户输入判断任务意图。

    Planner v2 的重点：

    read、search、analyze、git、plan
    可以继续使用基础关键词。

    edit 和 test 必须通过更严格的
    上下文规则判断。
    """

    text = normalize_task_text(
        user_message
    )

    if not text:
        return [TASK_INTENT_ANALYZE]

    intents: list[str] = []

    basic_intents = [
        TASK_INTENT_READ,
        TASK_INTENT_SEARCH,
        TASK_INTENT_ANALYZE,
        TASK_INTENT_GIT,
        TASK_INTENT_PLAN,
    ]

    for intent in basic_intents:
        keywords = INTENT_KEYWORDS[intent]

        if contains_any_keyword(
            text,
            keywords,
        ):
            intents.append(intent)

    if is_explicit_edit_request(text):
        intents.append(
            TASK_INTENT_EDIT
        )

    if is_explicit_test_request(text):
        intents.append(
            TASK_INTENT_TEST
        )

    # 即使没有出现“分析”二字，
    # “如果修改会影响什么”本质上也是分析任务。
    if (
        is_analysis_only_edit_context(text)
        and TASK_INTENT_ANALYZE
        not in intents
    ):
        intents.append(
            TASK_INTENT_ANALYZE
        )

    if (
        is_advisory_check_context(text)
        and TASK_INTENT_ANALYZE
        not in intents
    ):
        intents.append(
            TASK_INTENT_ANALYZE
        )

    if not intents:
        intents.append(
            TASK_INTENT_ANALYZE
        )

    return deduplicate_keep_order(
        intents
    )


def deduplicate_keep_order(
    items: list[str],
) -> list[str]:
    """
    去重，但保持原顺序。

    不能直接使用 set，
    因为 set 不保证原始顺序。
    """

    seen: set[str] = set()
    result: list[str] = []

    for item in items:
        if item in seen:
            continue

        seen.add(item)
        result.append(item)

    return result


def extract_target_paths(
    user_message: str,
) -> list[str]:
    """
    从用户任务中提取可能的文件或目录路径。

    支持：
    - demo_project/main.py
    - `demo_project/main.py`
    - app/agent/agent_loop.py
    - tests/test_xxx.py
    """

    text = normalize_task_text(
        user_message
    )

    if not text:
        return []

    path_pattern = (
        r"`?"
        r"([A-Za-z0-9_\-./\\]+"
        r"(?:\.py|\.md|\.json|\.txt|"
        r"\.yaml|\.yml|/|\\)"
        r"[A-Za-z0-9_\-./\\]*)"
        r"`?"
    )

    matches = re.findall(
        path_pattern,
        text,
    )

    cleaned_paths: list[str] = []

    for path in matches:
        cleaned = (
            path
            .strip("`")
            .strip()
        )

        if not cleaned:
            continue

        if cleaned in {".", "./"}:
            continue

        cleaned_paths.append(cleaned)

    return deduplicate_keep_order(
        cleaned_paths
    )


def estimate_task_complexity(
    intents: list[str],
    target_paths: list[str],
    user_message: str,
) -> str:
    """
    粗略估计任务复杂度。

    simple：
    - 单纯读取一个文件；
    - 简单查看状态。

    medium：
    - 符号搜索；
    - 结构分析；
    - 依赖和影响分析；
    - 普通测试。

    complex：
    - 修改代码；
    - 修改并测试；
    - 完整项目分析。
    """

    text = normalize_task_text(
        user_message
    )

    context = detect_task_context(
        text
    )

    if TASK_INTENT_EDIT in intents:
        return TASK_COMPLEXITY_COMPLEX

    if (
        TASK_INTENT_TEST in intents
        and len(intents) >= 2
    ):
        return TASK_COMPLEXITY_COMPLEX

    if (
        "所有文件" in text
        or "完整分析" in text
        or "全部分析" in text
        or "潜在问题" in text
    ):
        return TASK_COMPLEXITY_COMPLEX

    if len(target_paths) >= 2:
        return TASK_COMPLEXITY_MEDIUM

    if (
        context["python_symbol_search"]
        or context["python_outline"]
        or context["python_dependencies"]
        or context["python_impact"]
    ):
        return TASK_COMPLEXITY_MEDIUM

    if (
        TASK_INTENT_ANALYZE in intents
        or TASK_INTENT_SEARCH in intents
        or TASK_INTENT_TEST in intents
    ):
        return TASK_COMPLEXITY_MEDIUM

    return TASK_COMPLEXITY_SIMPLE


def estimate_tools_for_intents(
    intents: list[str],
    user_message: str = "",
) -> list[str]:
    """
    根据任务意图和上下文推荐工具。

    注意：
    这里只生成计划，不执行工具。

    user_message 设置默认值，
    是为了尽量兼容旧测试或旧调用代码。
    """

    tools: list[str] = []

    context = detect_task_context(
        user_message
    )

    has_special_analysis_tool = False

    # 优先选择最具体的代码理解工具。
    if context["python_impact"]:
        tools.append(
            "analyze_python_impact"
        )
        has_special_analysis_tool = True

    elif context["python_dependencies"]:
        tools.append(
            "get_python_dependencies"
        )
        has_special_analysis_tool = True

    elif context["python_outline"]:
        tools.append(
            "get_python_file_outline"
        )
        has_special_analysis_tool = True

    elif context["python_symbol_search"]:
        tools.append(
            "search_python_symbol"
        )
        has_special_analysis_tool = True

    if TASK_INTENT_PLAN in intents:
        tools.append(
            "list_files"
        )

    if TASK_INTENT_SEARCH in intents:
        if not has_special_analysis_tool:
            tools.extend([
                "list_files",
                "search_code",
            ])

    if TASK_INTENT_ANALYZE in intents:
        if not has_special_analysis_tool:
            tools.extend([
                "list_files",
                "read_file",
                "search_code",
            ])

    if TASK_INTENT_READ in intents:
        if context["python_symbol_search"]:
            tools.append(
                "read_file_lines"
            )

        elif (
            context["python_impact"]
            or context[
                "python_dependencies"
            ]
        ):
            tools.append(
                "read_file"
            )

        elif not context["python_outline"]:
            tools.extend([
                "list_files",
                "read_file",
            ])

    if TASK_INTENT_EDIT in intents:
        # 修改前先了解影响范围。
        if (
            context["python_impact"]
            and "analyze_python_impact"
            not in tools
        ):
            tools.append(
                "analyze_python_impact"
            )

        if context["new_file_request"]:
            tools.append(
                "write_new_file"
            )
        else:
            tools.extend([
                "read_file",
                "edit_file",
            ])

        tools.append(
            "get_workspace_diff"
        )

    if TASK_INTENT_TEST in intents:
        tools.append(
            "run_command"
        )

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

    return deduplicate_keep_order(
        tools
    )


def estimate_plan_risk(
    tools: list[str],
) -> str:
    """
    根据推荐工具估计风险。

    high：
    - edit_file；
    - write_new_file；
    - 其他写操作。

    medium：
    - run_command。

    low：
    - 只读分析工具。
    """

    risk_levels = [
        get_tool_risk_level(tool)
        for tool in tools
    ]

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
    """

    if suggested_tool:
        risk_level = (
            get_tool_risk_level(
                suggested_tool
            )
        )
    else:
        risk_level = TASK_RISK_LOW

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
    user_message: str = "",
) -> list[dict[str, Any]]:
    """
    根据任务意图和上下文生成执行步骤。

    Planner v2 会优先生成更具体的代码理解步骤，
    而不是统一使用 list_files + search_code。
    """

    steps: list[dict[str, Any]] = []
    used_tools: set[str] = set()
    index = 1

    context = detect_task_context(
        user_message
    )

    has_target = bool(target_paths)

    if has_target:
        target_text = "、".join(
            target_paths
        )
    else:
        target_text = "目标工作区"

    def append_step(
        *,
        title: str,
        description: str,
        suggested_tool: str | None,
        reason: str,
    ) -> None:
        """
        内部辅助函数：
        增加步骤并自动更新 index。
        """

        nonlocal index

        steps.append(
            build_plan_step(
                index=index,
                title=title,
                description=description,
                suggested_tool=suggested_tool,
                reason=reason,
            )
        )

        if suggested_tool:
            used_tools.add(
                suggested_tool
            )

        index += 1

    # --------------------------------------------------------
    # 专用分析步骤
    # --------------------------------------------------------

    if context["python_impact"]:
        append_step(
            title="分析 Python 影响范围",
            description=(
                f"分析 {target_text} 的反向 import "
                "依赖，识别直接和间接受影响文件。"
            ),
            suggested_tool=(
                "analyze_python_impact"
            ),
            reason=(
                "用户询问文件修改影响或依赖方，"
                "应优先使用反向依赖分析工具。"
            ),
        )

    elif context["python_dependencies"]:
        append_step(
            title="分析 Python 文件依赖",
            description=(
                f"分析 {target_text} 导入了哪些"
                "本地 Python 模块。"
            ),
            suggested_tool=(
                "get_python_dependencies"
            ),
            reason=(
                "用户询问当前文件依赖谁，"
                "应使用正向依赖分析工具。"
            ),
        )

    elif context["python_outline"]:
        append_step(
            title="分析 Python 文件结构",
            description=(
                f"提取 {target_text} 中的 import、"
                "类、函数和方法结构。"
            ),
            suggested_tool=(
                "get_python_file_outline"
            ),
            reason=(
                "AST 文件大纲工具比普通文本"
                "搜索更适合分析代码结构。"
            ),
        )

    elif context["python_symbol_search"]:
        append_step(
            title="搜索 Python 符号定义",
            description=(
                "使用 AST 搜索真实的 Python "
                "类、函数或方法定义位置。"
            ),
            suggested_tool=(
                "search_python_symbol"
            ),
            reason=(
                "符号搜索可以排除注释、字符串"
                "和普通文本中的同名内容。"
            ),
        )

    # --------------------------------------------------------
    # 通用计划和搜索
    # --------------------------------------------------------

    if (
        TASK_INTENT_PLAN in intents
        and "list_files" not in used_tools
    ):
        append_step(
            title="查看项目结构",
            description=(
                f"查看 {target_text} 的目录结构，"
                "确认任务范围。"
            ),
            suggested_tool="list_files",
            reason=(
                "规划任务前需要了解项目结构。"
            ),
        )

    if (
        TASK_INTENT_SEARCH in intents
        and not (
            context["python_impact"]
            or context[
                "python_dependencies"
            ]
            or context["python_outline"]
            or context[
                "python_symbol_search"
            ]
        )
    ):
        append_step(
            title="搜索相关代码",
            description=(
                "根据用户任务中的关键词"
                "搜索相关代码位置。"
            ),
            suggested_tool="search_code",
            reason=(
                "普通关键词、调用位置、字符串"
                "和配置项适合使用文本搜索。"
            ),
        )

    # --------------------------------------------------------
    # 读取步骤
    # --------------------------------------------------------

    if TASK_INTENT_READ in intents:
        if context["python_symbol_search"]:
            append_step(
                title="读取符号实现代码",
                description=(
                    "根据符号搜索返回的起止行号，"
                    "读取目标符号的局部代码。"
                ),
                suggested_tool=(
                    "read_file_lines"
                ),
                reason=(
                    "局部读取可以减少无关代码"
                    "和 Token 消耗。"
                ),
            )

        elif (
            context["python_impact"]
            or context[
                "python_dependencies"
            ]
        ):
            append_step(
                title="读取相关依赖文件",
                description=(
                    "读取依赖分析得到的关键文件，"
                    "确认实际使用方式和兼容风险。"
                ),
                suggested_tool="read_file",
                reason=(
                    "依赖关系只表示文件关系，"
                    "还需要读取代码确认实际使用点。"
                ),
            )

        elif not context["python_outline"]:
            append_step(
                title="读取关键文件",
                description=(
                    f"读取与任务有关的关键文件："
                    f"{target_text}。"
                ),
                suggested_tool="read_file",
                reason=(
                    "读取真实内容后才能进行"
                    "准确分析。"
                ),
            )

    # --------------------------------------------------------
    # 普通分析兜底
    # --------------------------------------------------------

    if (
        TASK_INTENT_ANALYZE in intents
        and not (
            context["python_impact"]
            or context[
                "python_dependencies"
            ]
            or context["python_outline"]
            or context[
                "python_symbol_search"
            ]
        )
        and "read_file" not in used_tools
    ):
        append_step(
            title="读取并分析关键文件",
            description=(
                f"读取 {target_text} 的真实内容，"
                "再进行结构和问题分析。"
            ),
            suggested_tool="read_file",
            reason=(
                "没有匹配专用分析工具时，"
                "先读取文件再分析。"
            ),
        )

    # --------------------------------------------------------
    # 编辑步骤
    # --------------------------------------------------------

    if TASK_INTENT_EDIT in intents:
        if context["new_file_request"]:
            append_step(
                title="创建新文件",
                description=(
                    "根据用户要求创建新的文件内容。"
                ),
                suggested_tool="write_new_file",
                reason=(
                    "用户明确要求创建或新建文件。"
                ),
            )

        else:
            if "read_file" not in used_tools:
                append_step(
                    title="读取修改目标",
                    description=(
                        f"修改前读取目标文件："
                        f"{target_text}。"
                    ),
                    suggested_tool="read_file",
                    reason=(
                        "修改前必须确认原始内容，"
                        "避免盲目修改。"
                    ),
                )

            append_step(
                title="执行代码修改",
                description=(
                    "根据用户明确要求修改文件内容。"
                ),
                suggested_tool="edit_file",
                reason=(
                    "用户明确要求执行写操作。"
                ),
            )

        append_step(
            title="查看修改差异",
            description=(
                "查看 workspace diff，"
                "确认实际修改范围。"
            ),
            suggested_tool=(
                "get_workspace_diff"
            ),
            reason=(
                "修改后应检查差异，"
                "避免误改其他内容。"
            ),
        )

    # --------------------------------------------------------
    # 测试步骤
    # --------------------------------------------------------

    if TASK_INTENT_TEST in intents:
        append_step(
            title="运行验证命令",
            description=(
                "运行用户要求的测试、语法检查"
                "或其他安全白名单命令。"
            ),
            suggested_tool="run_command",
            reason=(
                "用户明确要求执行测试或验证。"
            ),
        )

    # --------------------------------------------------------
    # Git 步骤
    # --------------------------------------------------------

    if TASK_INTENT_GIT in intents:
        append_step(
            title="查看 Git 状态",
            description=(
                "查看当前 Git 工作区状态。"
            ),
            suggested_tool="get_git_status",
            reason=(
                "Git 状态可以确认哪些文件"
                "发生了变化。"
            ),
        )

        append_step(
            title="查看 Git 差异",
            description=(
                "查看 Git diff，"
                "检查具体代码变化。"
            ),
            suggested_tool="get_git_diff",
            reason=(
                "提交或总结前应检查完整差异。"
            ),
        )

    # --------------------------------------------------------
    # 复杂任务汇总
    # --------------------------------------------------------

    if complexity == TASK_COMPLEXITY_COMPLEX:
        append_step(
            title="汇总结果与风险",
            description=(
                "总结执行结果、实际修改、"
                "验证结果和潜在风险。"
            ),
            suggested_tool=None,
            reason=(
                "复杂任务需要最终汇总，"
                "方便用户审查。"
            ),
        )

    return steps


def build_task_warnings(
    intents: list[str],
    risk_level: str,
    complexity: str,
) -> list[str]:
    """
    根据风险和复杂度生成提醒。
    """

    warnings: list[str] = []

    if risk_level == TASK_RISK_HIGH:
        warnings.append(
            "该任务涉及写文件操作，"
            "高风险工具执行前需要用户审批。"
        )

    if risk_level == TASK_RISK_MEDIUM:
        warnings.append(
            "该任务涉及命令执行，"
            "需要注意命令白名单和执行目录。"
        )

    if (
        complexity
        == TASK_COMPLEXITY_COMPLEX
    ):
        warnings.append(
            "该任务较复杂，建议分步骤执行，"
            "并在关键节点检查结果。"
        )

    if (
        TASK_INTENT_EDIT in intents
        and TASK_INTENT_TEST
        not in intents
    ):
        warnings.append(
            "任务包含代码修改，但用户未明确"
            "要求执行测试；可以在修改后建议"
            "用户进行验证。"
        )

    return warnings


def build_task_plan(
    user_message: str,
) -> dict[str, Any]:
    """
    构建 Task Planner v2 结果。

    输入：
    - 用户自然语言任务。

    输出：
    - 意图；
    - 路径；
    - 推荐工具；
    - 风险；
    - 复杂度；
    - 执行步骤；
    - 警告。
    """

    normalized_message = (
        normalize_task_text(
            user_message
        )
    )

    intents = detect_task_intents(
        normalized_message
    )

    target_paths = extract_target_paths(
        normalized_message
    )

    tools = estimate_tools_for_intents(
        intents,
        normalized_message,
    )

    risk_level = estimate_plan_risk(
        tools
    )

    complexity = (
        estimate_task_complexity(
            intents=intents,
            target_paths=target_paths,
            user_message=(
                normalized_message
            ),
        )
    )

    steps = build_steps_for_plan(
        intents=intents,
        target_paths=target_paths,
        complexity=complexity,
        user_message=normalized_message,
    )

    warnings = build_task_warnings(
        intents=intents,
        risk_level=risk_level,
        complexity=complexity,
    )

    planner_meta = build_planner_meta(
        user_message=normalized_message,
        intents=intents,
        tools=tools,
        risk_level=risk_level,
    )

    return {
        "objective": normalized_message,
        "intents": intents,
        "target_paths": target_paths,
        "suggested_tools": tools,
        "risk_level": risk_level,
        "complexity": complexity,
        "needs_approval": (
            risk_level
            == TASK_RISK_HIGH
        ),
        "estimated_steps": len(steps),
        "steps": steps,
        "warnings": warnings,

        # Planner v2.1 新增：
        # 不改变原有字段，
        # 只增加可解释元数据。
        "planner_meta": planner_meta,
    }