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