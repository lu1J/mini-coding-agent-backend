"""Day19 Agent Eval：离线重评分 / 归档解析 / cleanup 测试。

约束验证：
- offline 只读归档重评分（不调用模型 / HTTP / 当前 workspace）；
- 重评分使用归档内的 task_snapshot（任务集变更不影响判定）；
- tasks_sha256 不一致时只警告不中断；
- 归档可被 resolve_archive 以 id / 文件名 / 路径解析；
- cleanup 只删除已有归档的 case 目录（无归档需 --force）。
"""

import json
from pathlib import Path

import pytest

from evals import report, runner, scoring
from evals.dataset import CASE_ROOT_RELATIVE


# ---------------------------------------------------------------- 构造归档


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _snapshot(path_to_text: dict[str, str]) -> dict:
    out = {}
    for rel, text in path_to_text.items():
        out[rel] = {"exists": True, "sha256": _sha(text), "content": text}
    return out


def _minimal_trace() -> dict:
    return {
        "invoke": {
            "status": "finished",
            "answer": "ok",
            "thread_id": "t1",
            "steps": [
                {
                    "type": "tool_call",
                    "tool_name": "read_file_lines",
                    "tool_args": {"path": "workspace/eval_cases/r/t/demo_project/math_utils.py"},
                    "success": True,
                    "tool_result": "def multiply(a, b):\n    return a * b",
                }
            ],
            "executor_state": {
                "total_steps": 1,
                "completed_steps": 1,
                "blocked_calls": 0,
                "supporting_calls": 0,
                "warning_steps": 0,
                "current_step_position": 1,
                "completion_status": "completed",
            },
        },
        "resumes": [],
        "http_errors": [],
        "notes": [],
    }


def _passing_task_entry(task_id: str = "t_read") -> dict:
    text = "def multiply(a, b):\n    return a * b\n"
    return {
        "task_id": task_id,
        "name": "读取任务",
        "category": "read",
        "smoke": True,
        "prompt": "请读取 workspace/eval_cases/r/t/demo_project/math_utils.py",
        "duration_ms": 1500.0,
        "task_snapshot": {
            "id": task_id,
            "name": "读取任务",
            "category": "read",
            "max_steps": 6,
            "prompt_template": "{case_root}",
            "expected": {
                "allowed_statuses": ["finished"],
                "required_tools": ["read_file_lines"],
                "forbidden_tools": ["edit_file"],
                "plan_completion_min": 1.0,
                "max_premature_final": 0,
                "executor_stalled_allowed": False,
            },
            "approval": {"flow": "none", "expected_pending_tool": None},
            "verify": [],
        },
        "trace": _minimal_trace(),
        "evidence": {"before": _snapshot({"math_utils.py": text}),
                     "approval": {},
                     "after": _snapshot({"math_utils.py": text})},
        "metrics": {},
        "checks": [],
        "errors": [],
        "passed": True,
        "verdict": "passed",
    }


def _write_fake_archive(
    monkeypatch,
    tmp_path: Path,
    *entries,
    run_id: str = "eval_20260905_120000",
    sha256: str | None = None,
):
    monkeypatch.setattr(report, "ARCHIVE_DIR", tmp_path)
    archive_dir = tmp_path
    archive = {
        "meta": {
            "eval_run_id": run_id,
            "timestamp": "2026-09-05T12:00:00",
            "mode": "smoke",
            "framework_version": "1.0.0",
            "git_commit": "abc123",
            "git_dirty": False,
            "model_name": "test-model",
            "tasks_sha256": sha256 or report.dataset_sha256(),
        },
        "tasks": list(entries),
    }
    path = tmp_path / f"{run_id}.json"
    path.write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------- resolve 归档


def test_resolve_archive_by_id_filename_and_path(monkeypatch, tmp_path: Path):
    path = _write_fake_archive(monkeypatch, tmp_path, _passing_task_entry())
    assert report.resolve_archive("eval_20260905_120000") == path
    assert report.resolve_archive("eval_20260905_120000.json") == path
    assert report.resolve_archive(str(path)) == path
    assert report.resolve_archive(str(path.parent / "eval_20260905_120000.json")) == path


def test_resolve_archive_missing_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(report, "ARCHIVE_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        report.resolve_archive("no_such_run")


def test_list_archive_ids(monkeypatch, tmp_path: Path):
    _write_fake_archive(monkeypatch, tmp_path, _passing_task_entry(), run_id="run_b")
    _write_fake_archive(monkeypatch, tmp_path, _passing_task_entry(), run_id="run_a")
    assert report.list_archive_ids() == ["run_a", "run_b"]


# ---------------------------------------------------------------- 离线重评分


def test_offline_rescore_all_pass_consistent(capsys, monkeypatch, tmp_path: Path):
    _write_fake_archive(monkeypatch, tmp_path, _passing_task_entry())
    summary = report.offline_rescore("eval_20260905_120000")
    assert summary["total"] == 1
    assert summary["passed"] == 1
    assert summary["archived_passed"] == 1
    assert summary["mismatch_count"] == 0
    assert summary["tasks_sha256_ok"] is True
    output = capsys.readouterr().out
    assert "全部通过" in output


def test_offline_rescore_detects_archived_verdict_mismatch(capsys, monkeypatch, tmp_path: Path):
    """归档说通过但证据其实不满足 → 重评分判 FAIL（PASS→FAIL）。"""
    entry = _passing_task_entry()
    # 篡改：归档 passed=True，但 trace 里没有调用 required_tool。
    entry["trace"] = {
        "invoke": {"status": "finished", "answer": "", "thread_id": "t1",
                   "executor_state": {"total_steps": 1, "completed_steps": 1}},
        "resumes": [],
        "http_errors": [],
        "notes": [],
    }
    _write_fake_archive(monkeypatch, tmp_path, entry)
    summary = report.offline_rescore("eval_20260905_120000")
    assert summary["passed"] == 0
    assert summary["mismatch_count"] == 1
    assert summary["mismatches"][0]["direction"] == "PASS → FAIL"
    output = capsys.readouterr().out
    assert "判定不一致" in output


def test_offline_rescore_uses_archived_task_snapshot(monkeypatch, tmp_path: Path):
    """重评分基于归档 task_snapshot（即使当前任务集不同也不影响判定）。"""
    calls = {}

    def _fake_score_task(task, trace, evidence, duration_ms=None):
        calls["task_id"] = (task or {}).get("id")
        return {"passed": True, "verdict": "passed", "errors": [], "checks": [],
                "metrics": {"task_success": True}}

    monkeypatch.setattr(scoring, "score_task", _fake_score_task)
    _write_fake_archive(monkeypatch, tmp_path, _passing_task_entry())
    report.offline_rescore("eval_20260905_120000")
    assert calls["task_id"] == "t_read"


def test_offline_rescore_sha_mismatch_warns_only(capsys, monkeypatch, tmp_path: Path):
    _write_fake_archive(
        monkeypatch,
        tmp_path,
        _passing_task_entry(),
        sha256="0" * 64,
    )
    summary = report.offline_rescore("eval_20260905_120000")
    assert summary["tasks_sha256_ok"] is False
    assert summary["passed"] == 1  # 仍正常重评分
    assert "不一致" in capsys.readouterr().out


def test_offline_rescore_never_reads_workspace(monkeypatch, tmp_path: Path):
    """offline 路径只读归档：把真实 ARCHIVE_DIR 与 workspace 隔离后仍可完成。"""
    workspace = tmp_path / "real_workspace"
    (workspace / "demo_project").mkdir(parents=True)
    (workspace / "demo_project" / "secret_file.txt").write_text("nope", encoding="utf-8")
    monkeypatch.setattr(report, "ARCHIVE_DIR", tmp_path / "archive_elsewhere")
    _write_fake_archive(monkeypatch, tmp_path, _passing_task_entry())
    summary = report.offline_rescore("eval_20260905_120000")
    assert summary["total"] == 1
    assert summary["passed"] == 1


def test_archive_round_trip_survives_json_serialization(monkeypatch, tmp_path: Path):
    """write_archive 写出的归档可被 json 读回，meta/tasks 结构完整。"""
    monkeypatch.setattr(report, "ARCHIVE_DIR", tmp_path)
    task_entry = _passing_task_entry()
    archive = report.build_archive(
        eval_run_id="eval_round_trip",
        mode="full",
        results=[task_entry],
    )
    path = report.write_archive("eval_round_trip", archive)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["meta"]["eval_run_id"] == "eval_round_trip"
    assert loaded["meta"]["mode"] == "full"
    assert loaded["tasks"][0]["task_snapshot"]["id"] == "t_read"
    assert loaded["tasks"][0]["evidence"]["after"]["math_utils.py"]["exists"] is True


# ---------------------------------------------------------------- 快照函数


def test_snapshot_tree_normalizes_crlf_and_marks_binary(tmp_path: Path):
    (tmp_path / "text.txt").write_bytes(b"line1\r\nline2\n")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\xff\xfe")
    (tmp_path / "skip.pyc").write_bytes(b"pyc")
    snap = runner.snapshot_tree(tmp_path)
    assert snap["text.txt"]["content"] == "line1\nline2\n"
    assert snap["blob.bin"]["content"] is None
    assert "skip.pyc" not in snap


# ---------------------------------------------------------------- cleanup


def test_cleanup_cases_removes_only_archived_runs(monkeypatch, tmp_path: Path):
    workspace = tmp_path
    monkeypatch.setattr(runner, "PROJECT_ROOT", workspace)
    monkeypatch.setattr(runner, "CASE_ROOT_RELATIVE", Path("eval_cases"))
    monkeypatch.setattr(report, "ARCHIVE_DIR", workspace / "archives")

    case_root = workspace / "eval_cases"
    (case_root / "run_with_archive" / "t1").mkdir(parents=True)
    (case_root / "run_without_archive" / "t1").mkdir(parents=True)
    report.ARCHIVE_DIR.mkdir(parents=True)
    (report.ARCHIVE_DIR / "run_with_archive.json").write_text("{}", encoding="utf-8")

    result = runner.cleanup_cases()
    assert result["removed"] == ["run_with_archive"]
    assert result["refused"] == ["run_without_archive"]
    assert not (case_root / "run_with_archive").exists()
    assert (case_root / "run_without_archive").exists()

    # --force 清掉无归档 run。
    result = runner.cleanup_cases(force=True)
    assert result["removed"] == ["run_without_archive"]
    assert not (case_root / "run_without_archive").exists()


def test_cleanup_cases_dry_run_removes_nothing(monkeypatch, tmp_path: Path):
    workspace = tmp_path
    monkeypatch.setattr(runner, "PROJECT_ROOT", workspace)
    monkeypatch.setattr(runner, "CASE_ROOT_RELATIVE", Path("eval_cases"))
    monkeypatch.setattr(report, "ARCHIVE_DIR", workspace / "archives")

    case_root = workspace / "eval_cases"
    (case_root / "run_x" / "t1").mkdir(parents=True)
    report.ARCHIVE_DIR.mkdir(parents=True)
    (report.ARCHIVE_DIR / "run_x.json").write_text("{}", encoding="utf-8")

    result = runner.cleanup_cases(dry_run=True)
    assert result["removed"] == []
    assert (case_root / "run_x").exists()

    result = runner.cleanup_cases(run_id="run_x")
    assert result["removed"] == ["run_x"]
    assert not (case_root / "run_x").exists()
