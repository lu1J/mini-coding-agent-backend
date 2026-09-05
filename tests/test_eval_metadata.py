import json
from datetime import datetime, timezone
from pathlib import Path

import run_eval


def test_read_project_version(
    monkeypatch,
    tmp_path: Path,
):
    """
    应该能够读取 VERSION 文件中的版本号。
    """

    version_file = tmp_path / "VERSION"
    version_file.write_text(
        "0.6.1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        run_eval,
        "VERSION_FILE",
        version_file,
    )

    assert run_eval.read_project_version() == "0.6.1"


def test_calculate_file_sha256_is_stable(
    tmp_path: Path,
):
    """
    相同文件内容应该产生相同的 SHA-256，
    不同内容应该产生不同的 SHA-256。
    """

    first_file = tmp_path / "first.json"
    second_file = tmp_path / "second.json"

    first_file.write_text(
        '{"task": 1}',
        encoding="utf-8",
    )
    second_file.write_text(
        '{"task": 1}',
        encoding="utf-8",
    )

    first_hash = run_eval.calculate_file_sha256(
        first_file
    )
    second_hash = run_eval.calculate_file_sha256(
        second_file
    )

    assert first_hash == second_hash

    second_file.write_text(
        '{"task": 2}',
        encoding="utf-8",
    )

    changed_hash = run_eval.calculate_file_sha256(
        second_file
    )

    assert changed_hash != first_hash


def test_eval_metadata_does_not_include_api_key(
    monkeypatch,
    tmp_path: Path,
):
    """
    Eval 元数据可以记录模型名称，
    但绝对不能保存 API Key。
    """

    task_file = tmp_path / "eval_tasks.json"
    task_file.write_text(
        "[]",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        run_eval,
        "TASK_FILE",
        task_file,
    )
    monkeypatch.setattr(
        run_eval,
        "read_project_version",
        lambda: "0.6.1",
    )
    monkeypatch.setattr(
        run_eval,
        "get_git_commit",
        lambda: "abc123",
    )
    monkeypatch.setattr(
        run_eval,
        "is_git_worktree_dirty",
        lambda: False,
    )

    monkeypatch.setenv(
        "DEEPSEEK_MODEL",
        "test-model",
    )
    monkeypatch.setenv(
        "DEEPSEEK_API_KEY",
        "secret-key-must-not-appear",
    )

    started_at = datetime(
        2026,
        7,
        29,
        8,
        0,
        tzinfo=timezone.utc,
    )
    finished_at = datetime(
        2026,
        7,
        29,
        8,
        1,
        tzinfo=timezone.utc,
    )

    metadata = run_eval.build_eval_metadata(
        started_at=started_at,
        finished_at=finished_at,
    )

    metadata_text = json.dumps(
        metadata,
        ensure_ascii=False,
    )

    assert metadata["model_name"] == "test-model"
    assert metadata["project_version"] == "0.6.1"

    assert "secret-key-must-not-appear" not in metadata_text
    assert "DEEPSEEK_API_KEY" not in metadata_text


# ---------------------------------------------------------------- Day19 扩展


def test_day19_meta_contains_required_keys_and_no_secret(
    monkeypatch,
):
    """
    Day19 Agent Eval 归档 meta 必须包含全部规范字段，
    序列化后绝不能出现 API key / Authorization / .env 内容。
    """

    from evals.report import build_meta

    monkeypatch.setenv("DEEPSEEK_MODEL", "day19-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-super-secret-day19")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid/v1")

    meta = build_meta(eval_run_id="eval_x", mode="smoke")
    meta_text = json.dumps(meta, ensure_ascii=False)

    assert meta["eval_run_id"] == "eval_x"
    assert meta["mode"] == "smoke"
    assert meta["framework_version"].startswith("1.")
    assert meta["model_name"] == "day19-model"
    assert len(str(meta["git_commit"] or "")) in (0, 40)
    assert isinstance(meta["git_dirty"], (bool, type(None)))
    assert isinstance(meta["tasks_sha256"], str) and len(meta["tasks_sha256"]) == 64
    assert isinstance(meta["timestamp"], str)

    assert "sk-super-secret-day19" not in meta_text
    assert "DEEPSEEK_API_KEY" not in meta_text
    assert "Authorization" not in meta_text
    assert ".env" not in meta_text


def test_day19_model_name_falls_back_to_unknown(
    monkeypatch,
):
    """DEEPSEEK_MODEL 未设置时 model_name 应回退为 unknown。"""

    from evals.report import build_meta

    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    meta = build_meta(eval_run_id="eval_y", mode="full")
    assert meta["model_name"] == "unknown"


def test_day19_archive_meta_never_contains_http_headers():
    """
    归档 meta 只含白名单字段；把 headers 之类任意塞进结果也不应出现
    在最终归档（build_archive 按字段挑选，不整体透传）。
    """
    import evals.report

    entry = {
        "task_id": "t1",
        "name": "n",
        "category": "read",
        "smoke": True,
        "prompt": "p",
        "duration_ms": 1.0,
        "task_snapshot": {"id": "t1"},
        "trace": {"invoke": None, "resumes": [], "http_errors": [], "notes": []},
        "evidence": {},
        "metrics": {},
        "checks": [],
        "errors": [],
        "passed": True,
        "verdict": "passed",
        "headers": {"Authorization": "Bearer should-not-archive"},
    }
    archive = evals.report.build_archive(
        eval_run_id="eval_z",
        mode="smoke",
        results=[entry],
    )
    archive_text = json.dumps(archive, ensure_ascii=False)
    assert "should-not-archive" not in archive_text
    assert "headers" not in archive_text


def test_day19_archive_dir_lives_under_gitignored_agent_runs():
    """归档目录约定：workspace/.agent_runs/evals（与 workspace/demo_project 并列隔离）。"""

    from evals.dataset import EVAL_ARCHIVE_RELATIVE

    assert EVAL_ARCHIVE_RELATIVE.as_posix() == "workspace/.agent_runs/evals"


def test_day19_legacy_eval_entry_still_kept():
    """
    legacy 兼容：run_eval.py / eval_tasks.json 保留；
    scripts/dev.py 无参 eval 仍路由到 run_eval.py。
    """
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    assert (project_root / "run_eval.py").is_file()
    assert (project_root / "eval_tasks.json").is_file()

    dev_py = (project_root / "scripts" / "dev.py").read_text(encoding="utf-8")
    assert "run_eval.py" in dev_py
    assert "legacy" in dev_py.lower()