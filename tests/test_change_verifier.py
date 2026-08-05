from pathlib import Path

import app.agent.change_verifier as verifier


def set_workspace(monkeypatch, tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(verifier, "WORKSPACE_ROOT", workspace.resolve())
    return workspace


def test_valid_python_change_passes(monkeypatch, tmp_path):
    workspace = set_workspace(monkeypatch, tmp_path)
    target = workspace / "demo" / "main.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")

    def edit_tool(path: str, old_text: str, new_text: str):
        file_path = workspace / path
        content = file_path.read_text(encoding="utf-8")
        file_path.write_text(content.replace(old_text, new_text), encoding="utf-8")
        return "文件修改成功"

    report = verifier.execute_verified_change(
        tool_name="edit_file",
        tool_args={
            "path": "demo/main.py",
            "old_text": "value = 1",
            "new_text": "value = 2",
        },
        tool_func=edit_tool,
    )

    assert report["success"] is True
    assert report["changed_paths"] == ["demo/main.py"]
    assert "value = 2" in target.read_text(encoding="utf-8")
    assert any(
        item["name"] == "python_syntax" and item["status"] == "passed"
        for item in report["checks"]
    )


def test_invalid_python_rolls_back_existing_file(monkeypatch, tmp_path):
    workspace = set_workspace(monkeypatch, tmp_path)
    target = workspace / "demo" / "main.py"
    target.parent.mkdir()
    original = "def hello():\n    return 'ok'\n"
    target.write_text(original, encoding="utf-8")

    def bad_edit(path: str, old_text: str, new_text: str):
        file_path = workspace / path
        file_path.write_text("def hello(:\n", encoding="utf-8")
        return "文件修改成功"

    report = verifier.execute_verified_change(
        tool_name="edit_file",
        tool_args={
            "path": "demo/main.py",
            "old_text": "return 'ok'",
            "new_text": "broken",
        },
        tool_func=bad_edit,
    )

    assert report["success"] is False
    assert report["status"] == verifier.VERIFY_STATUS_FAILED_ROLLED_BACK
    assert report["rollback"]["success"] is True
    assert target.read_text(encoding="utf-8") == original


def test_invalid_new_python_file_is_removed(monkeypatch, tmp_path):
    workspace = set_workspace(monkeypatch, tmp_path)
    (workspace / "demo").mkdir()
    target = workspace / "demo" / "new_file.py"

    def create_bad_file(path: str, content: str):
        file_path = workspace / path
        file_path.write_text(content, encoding="utf-8")
        return "文件创建成功"

    report = verifier.execute_verified_change(
        tool_name="write_new_file",
        tool_args={
            "path": "demo/new_file.py",
            "content": "def broken(:\n",
        },
        tool_func=create_bad_file,
    )

    assert report["success"] is False
    assert report["rollback"]["success"] is True
    assert target.exists() is False


def test_invalid_json_is_rolled_back(monkeypatch, tmp_path):
    workspace = set_workspace(monkeypatch, tmp_path)
    target = workspace / "demo" / "config.json"
    target.parent.mkdir()
    original = '{"enabled": true}\n'
    target.write_text(original, encoding="utf-8")

    def edit_json(path: str, old_text: str, new_text: str):
        file_path = workspace / path
        file_path.write_text('{"enabled": }', encoding="utf-8")
        return "文件修改成功"

    report = verifier.execute_verified_change(
        tool_name="edit_file",
        tool_args={
            "path": "demo/config.json",
            "old_text": "true",
            "new_text": "",
        },
        tool_func=edit_json,
    )

    assert report["success"] is False
    assert target.read_text(encoding="utf-8") == original
    assert any(
        item["name"] == "json_parse" and item["status"] == "failed"
        for item in report["checks"]
    )


def test_tool_failure_does_not_leave_partial_change(monkeypatch, tmp_path):
    workspace = set_workspace(monkeypatch, tmp_path)
    target = workspace / "demo" / "main.py"
    target.parent.mkdir()
    original = "value = 1\n"
    target.write_text(original, encoding="utf-8")

    def partial_failure(path: str, old_text: str, new_text: str):
        file_path = workspace / path
        file_path.write_text("value = 999\n", encoding="utf-8")
        return {
            "success": False,
            "result": "修改中途失败",
            "error": {"type": "partial_failure"},
        }

    report = verifier.execute_verified_change(
        tool_name="edit_file",
        tool_args={
            "path": "demo/main.py",
            "old_text": "value = 1",
            "new_text": "value = 2",
        },
        tool_func=partial_failure,
    )

    assert report["success"] is False
    assert report["rollback"]["success"] is True
    assert target.read_text(encoding="utf-8") == original


def test_missing_path_fails_closed(monkeypatch, tmp_path):
    set_workspace(monkeypatch, tmp_path)

    report = verifier.execute_verified_change(
        tool_name="edit_file",
        tool_args={"old_text": "a", "new_text": "b"},
        tool_func=lambda **kwargs: "不应该执行",
    )

    assert report["success"] is False
    assert report["status"] == verifier.VERIFY_STATUS_FAILED
    assert report["tool_execution"] is None


def test_edit_backup_side_effect_is_removed_on_rollback(monkeypatch, tmp_path):
    workspace = set_workspace(monkeypatch, tmp_path)
    target = workspace / "demo" / "main.py"
    backup = workspace / "demo" / "main.py.bak"
    target.parent.mkdir()
    original = "value = 1\n"
    target.write_text(original, encoding="utf-8")

    def edit_with_backup(path: str, old_text: str, new_text: str):
        file_path = workspace / path
        old_content = file_path.read_text(encoding="utf-8")
        file_path.with_suffix(file_path.suffix + ".bak").write_text(
            old_content,
            encoding="utf-8",
        )
        file_path.write_text("def broken(:\n", encoding="utf-8")
        return "文件修改成功"

    report = verifier.execute_verified_change(
        tool_name="edit_file",
        tool_args={
            "path": "demo/main.py",
            "old_text": "value = 1",
            "new_text": "value = 2",
        },
        tool_func=edit_with_backup,
    )

    assert report["success"] is False
    assert report["rollback"]["success"] is True
    assert target.read_text(encoding="utf-8") == original
    assert backup.exists() is False


def test_structured_tool_result_supports_tool_result_key(monkeypatch, tmp_path):
    workspace = set_workspace(monkeypatch, tmp_path)
    target = workspace / "demo" / "main.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")

    def structured_edit(path: str, old_text: str, new_text: str):
        file_path = workspace / path
        file_path.write_text("value = 2\n", encoding="utf-8")
        return {
            "success": True,
            "tool_result": "结构化修改成功",
            "error": None,
        }

    report = verifier.execute_verified_change(
        tool_name="edit_file",
        tool_args={
            "path": "demo/main.py",
            "old_text": "value = 1",
            "new_text": "value = 2",
        },
        tool_func=structured_edit,
    )

    assert report["success"] is True
    assert report["tool_execution"]["result"] == "结构化修改成功"
