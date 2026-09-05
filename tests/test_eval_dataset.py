"""Day19 Agent Eval：Golden Dataset / fixture / 隔离目录结构测试。

覆盖场景：
- golden_v1.json 合法 JSON、schema_version、任务数量与必填字段；
- 任务 id 唯一、分类与 verify 类型都落在封闭枚举内；
- approval 流声明约束（approve/reject 需要 expected_pending_tool）；
- smoke 恰好是 3 个固定任务；
- validate_dataset / validate_task 对非法数据报错；
- dataset_sha256 稳定且对内容变化敏感；
- fixture 文件齐全、无字节码残留；
- case 目录隔离约定与全新副本复制（不触碰真实 workspace）。
"""

import json
from pathlib import Path

from evals import dataset
from evals.dataset import (
    ALLOWED_APPROVAL_FLOWS,
    ALLOWED_CATEGORIES,
    ALLOWED_VERIFY_TYPES,
    FIXTURE_EXPECTED_FILES,
    case_dirs,
    copy_fixture_into_case,
    dataset_sha256,
    load_golden_tasks,
    validate_dataset,
    validate_task,
)


def _all_tasks():
    return load_golden_tasks()


# ------------------------------------------------------------------ 数据集本体


def test_golden_json_is_valid_and_schema_version_1():
    """golden_v1.json 必须是合法 JSON，schema_version == 1。"""
    raw = dataset.TASKS_FILE.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["schema_version"] == 1
    assert isinstance(data["tasks"], list)


def test_golden_has_16_tasks_and_unique_ids():
    tasks = _all_tasks()
    assert len(tasks) == 16
    ids = [task["id"] for task in tasks]
    assert len(ids) == len(set(ids))


def test_every_task_has_required_top_fields():
    """每个任务都带必填字段，prompt_template 含 {case_root} 占位符。"""
    for task in _all_tasks():
        for field in ("id", "name", "category", "max_steps", "prompt_template",
                      "expected", "approval", "verify"):
            assert task.get(field) is not None, f"{task.get('id')} 缺 {field}"
        assert isinstance(task["expected"], dict)
        assert isinstance(task["approval"], dict)
        assert isinstance(task["verify"], list)
        assert "{case_root}" in task["prompt_template"]


def test_categories_within_allowed_enum_and_declared_spread():
    """分类都合法，且覆盖 14 个声明的类别（含 2×read / 2×failure_recovery）。"""
    categories = [task["category"] for task in _all_tasks()]
    assert set(categories) <= ALLOWED_CATEGORIES
    from collections import Counter

    counts = Counter(categories)
    assert counts["read"] == 2
    assert counts["failure_recovery"] == 2
    # 规范要求的类别至少各 1 个：
    for required in (
        "search", "structure", "dependency", "modify_approval",
        "create_file_approval", "modify_verify", "verify_command",
        "modify_approval_reject", "multi_step", "max_steps_guard",
        "execution_guardrail", "diff_verify",
    ):
        assert counts[required] >= 1, f"缺少类别：{required}"


def test_verify_types_within_allowed_enum():
    for task in _all_tasks():
        for item in task["verify"]:
            assert item["type"] in ALLOWED_VERIFY_TYPES


def test_expected_allowed_statuses_non_empty():
    for task in _all_tasks():
        statuses = task["expected"].get("allowed_statuses")
        assert statuses, f"{task['id']} allowed_statuses 为空"
        assert isinstance(statuses, list)


def test_approval_flows_declared_correctly():
    """approve/reject 任务必须声明 expected_pending_tool；smoke 三类齐全。"""
    for task in _all_tasks():
        approval = task["approval"]
        assert approval["flow"] in ALLOWED_APPROVAL_FLOWS
        if approval["flow"] != "none":
            assert approval.get("expected_pending_tool"), (
                f"{task['id']} 审批任务缺少 expected_pending_tool"
            )
    smoke_ids = sorted(task["id"] for task in _all_tasks() if task.get("smoke"))
    assert smoke_ids == [
        "gv1_modify_docstring_approve",
        "gv1_read_math_utils",
        "gv1_search_hello",
    ]


def test_validate_dataset_passes_for_golden():
    assert validate_dataset(_all_tasks()) == []


def test_validate_task_rejects_duplicate_and_bad_enum():
    """非法任务会被 validate 报错：重复 id / 非法 category / verify 缺 path。"""
    problems = validate_dataset(_all_tasks() + [dict(_all_tasks()[0], id=_all_tasks()[0]["id"])])
    assert any("重复" in problem for problem in problems)

    bad_category = dict(_all_tasks()[0], category="not_a_category")
    assert any("category" in problem for problem in validate_task(bad_category, 0))

    bad_verify = dict(
        _all_tasks()[0],
        verify=[{"type": "file_exists"}],  # 缺 path
    )
    assert any("path" in problem for problem in validate_task(bad_verify, 0))

    bad_flow = dict(_all_tasks()[0], approval={"flow": "maybe"})
    assert any("flow" in problem for problem in validate_task(bad_flow, 0))

    no_placeholder = dict(_all_tasks()[0], prompt_template="没有占位符")
    assert any("{case_root}" in problem for problem in validate_task(no_placeholder, 0))

    # max_steps 与后端契约耦合（AgentRequest 上限 10），越界要报错。
    too_many_steps = dict(_all_tasks()[0], max_steps=12)
    assert any("max_steps" in problem for problem in validate_task(too_many_steps, 0))


# ------------------------------------------------------------------ sha256


def test_dataset_sha256_stable_and_content_sensitive(monkeypatch, tmp_path: Path):
    task_file = tmp_path / "dataset.json"
    task_file.write_text('{"tasks": [{"id": "a"}]}', encoding="utf-8")
    monkeypatch.setattr(dataset, "TASKS_FILE", task_file)

    first = dataset_sha256()
    second = dataset_sha256()
    assert first == second

    task_file.write_text('{"tasks": [{"id": "b"}]}', encoding="utf-8")
    assert dataset_sha256() != first


# ------------------------------------------------------------------ fixture


def test_fixture_files_complete_and_no_bytecode(monkeypatch, tmp_path: Path):
    """fixture 10 个文件齐全；目录里没有 __pycache__ / .pyc / .bak 残留。"""
    for relative in FIXTURE_EXPECTED_FILES:
        assert (dataset.FIXTURE_DIR / relative).is_file(), f"缺 fixture 文件：{relative}"
    assert dataset.fixture_files_exist() == []
    for cache in dataset.FIXTURE_DIR.rglob("__pycache__"):
        assert False, f"fixture 不应包含 __pycache__：{cache}"
    for pyc in dataset.FIXTURE_DIR.rglob("*.pyc"):
        assert False, f"fixture 不应包含 .pyc：{pyc}"


# ------------------------------------------------------------------ 隔离目录


def test_case_dirs_layout():
    """case 目录约定：workspace/eval_cases/<run_id>/<task_id>/demo_project。"""
    absolute, relative = case_dirs("run_x", "task_y")
    assert relative == "workspace/eval_cases/run_x/task_y/demo_project"
    assert absolute.is_absolute()
    assert absolute.name == "demo_project"
    assert absolute.parent.parent.name == "run_x"
    assert absolute.parent.name == "task_y"


def test_copy_fixture_is_fresh_isolated_copy(monkeypatch, tmp_path: Path):
    """每次复制产生全新独立副本；重复复制会清空旧内容。"""
    fake_fixture = tmp_path / "fixture"
    (fake_fixture / "src").mkdir(parents=True)
    (fake_fixture / "src" / "mod.py").write_text("value=1\n", encoding="utf-8")
    (fake_fixture / "README.md").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(dataset, "FIXTURE_DIR", fake_fixture)

    first_case = tmp_path / "case_a" / "demo_project"
    copy_fixture_into_case(first_case)
    assert (first_case / "src" / "mod.py").exists()

    # 修改第一份副本，不应影响重新复制的第二份。
    (first_case / "src" / "mod.py").write_text("value=999\n", encoding="utf-8")
    second_case = tmp_path / "case_b" / "demo_project"
    copy_fixture_into_case(second_case)
    assert (second_case / "src" / "mod.py").read_text(encoding="utf-8") == "value=1\n"

    # 对同一目标再次复制 = 全新覆盖。
    copy_fixture_into_case(second_case)
    assert (second_case / "README.md").exists()


def test_copy_fixture_never_touches_daily_workspace():
    """隔离 case 目录必然经过 eval_cases，绝不可能是日常 demo_project。"""
    _, relative = case_dirs("r", "t")
    assert "eval_cases" in relative
    assert "demo_project" in relative
    assert relative.count("demo_project") == 1
    # runner/工具层使用 workspace 相对路径定位，case 都在 workspace 内：
    assert relative.startswith("workspace/")
