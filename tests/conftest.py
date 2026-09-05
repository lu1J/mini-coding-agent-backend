"""隔离旧测试的文件副作用；真实 MCP 子进程使用临时源码副本。"""

import shutil
import socket
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    test_root = tmp_path / "isolated"
    workspace = test_root / "workspace"
    shutil.copytree(PROJECT_ROOT / "evals/fixtures/base_project", workspace / "demo_project")
    (workspace / "test_project").mkdir()
    monkeypatch.chdir(test_root)
    # 模块在 collection 时已经导入，需同步替换缓存的绝对路径及测试别名。
    original = PROJECT_ROOT / "workspace"
    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", "")
        if not (name.startswith("app.") or name.startswith("test_")):
            continue
        for key, value in list(vars(module).items()):
            if isinstance(value, Path) and value.is_absolute() and value.is_relative_to(original):
                monkeypatch.setattr(module, key, workspace / value.relative_to(original))
    import app.mcp.server as server_module

    monkeypatch.setattr(server_module, "PROJECT_ROOT", test_root)


@pytest.fixture
def mcp_project_root(tmp_path):
    shutil.copytree(PROJECT_ROOT / "app", tmp_path / "isolated" / "app",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return tmp_path / "isolated"


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    # Windows asyncio 的 socketpair 需要 loopback；禁止外部网络连接。
    def guard(original):
        def connect(sock, address, *args, **kwargs):
            if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
                raise AssertionError("pytest 禁止外网连接，请使用现有 fake/mock")
            return original(sock, address, *args, **kwargs)
        return connect

    monkeypatch.setattr(socket.socket, "connect", guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(socket.socket.connect_ex))
