from app.tools import command_runtime


def test_python_command_uses_current_interpreter(monkeypatch):
    monkeypatch.setattr(command_runtime.sys, "executable", "X:/venv/python.exe")

    parts = command_runtime.resolve_runtime_command_parts(
        ["python", "-m", "pytest", "-q"]
    )

    assert parts == ["X:/venv/python.exe", "-m", "pytest", "-q"]


def test_pytest_alias_uses_python_module(monkeypatch):
    monkeypatch.setattr(command_runtime.sys, "executable", "X:/venv/python.exe")

    parts = command_runtime.resolve_runtime_command_parts(["pytest", "-q"])

    assert parts == ["X:/venv/python.exe", "-m", "pytest", "-q"]
