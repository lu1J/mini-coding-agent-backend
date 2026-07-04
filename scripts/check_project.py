import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


REQUIRED_FILES = [
    "main.py",
    "requirements.txt",
    ".env.example",
    ".gitignore",
    "Dockerfile",
    ".dockerignore",
    "README.md",
    "docs/API.md",
    "docs/ARCHITECTURE.md",
    "docs/SECURITY.md",
    "docs/EVALUATION.md",
    "docs/ROADMAP.md",
    "pytest.ini",
    "eval_tasks.json",
    "run_eval.py",
    "scripts/check_project.py",
    "scripts/check_release.py",
    "scripts/dev.py",
    "scripts/setup_demo_workspace.py",
    "app/schemas.py",
    "app/llm/deepseek_client.py",
    "app/agent/agent_loop.py",
    "app/agent/code_agent.py",
    "app/agent/agent_stream.py",
    "app/agent/tool_policy.py",
    "app/agent/approval_store.py",
    "app/agent/run_logger.py",
    "app/tools/file_tools.py",
    "tests/test_file_tools.py",
    "tests/test_git_tools.py",
    "tests/test_approval_store.py",
    "tests/test_run_logger.py",
]


REQUIRED_REQUIREMENTS = [
    "fastapi",
    "uvicorn",
    "openai",
    "python-dotenv",
    "pydantic",
    "tzdata",
    "pytest",
    "requests",
]


REQUIRED_GITIGNORE_PATTERNS = [
    ".env",
    ".venv/",
    "__pycache__/",
    "*.pyc",
    "*.bak",
    "workspace/.agent_runs/",
    "workspace/.agent_pending/",
    "workspace/demo_project/",
    "eval_result.json",
]


def print_section(title: str):
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def normalize_requirement_line(line: str) -> str:
    """
    将 requirements.txt 中的一行依赖规范化。

    示例：
    uvicorn[standard] -> uvicorn
    fastapi==0.138.1 -> fastapi
    requests>=2.0.0 -> requests
    """
    line = line.replace("\ufeff", "").replace("\x00", "").strip().lower()

    if not line or line.startswith("#"):
        return ""

    for separator in ["==", ">=", "<=", "~=", ">", "<"]:
        if separator in line:
            line = line.split(separator)[0].strip()

    if "[" in line:
        line = line.split("[")[0].strip()

    return line


def check_required_files() -> bool:
    print_section("1. 检查关键文件")

    ok = True

    for relative_path in REQUIRED_FILES:
        path = PROJECT_ROOT / relative_path
        if path.exists():
            print(f"✅ {relative_path}")
        else:
            print(f"❌ 缺少文件：{relative_path}")
            ok = False

    return ok


def check_env_files() -> bool:
    print_section("2. 检查环境变量文件")

    env_path = PROJECT_ROOT / ".env"
    env_example_path = PROJECT_ROOT / ".env.example"

    ok = True

    if env_example_path.exists():
        print("✅ .env.example 存在")
    else:
        print("❌ 缺少 .env.example")
        ok = False

    if env_path.exists():
        print("✅ .env 存在")
    else:
        print("⚠️  .env 不存在：如果要调用真实模型，需要复制 .env.example 为 .env 并填写 API Key")

    if env_example_path.exists():
        content = env_example_path.read_text(encoding="utf-8", errors="ignore")

        if "your_deepseek_api_key_here" in content:
            print("✅ .env.example 未包含真实 API Key")
        else:
            print("⚠️  .env.example 中没有发现占位符，请确认没有写入真实 API Key")

    return ok


def check_requirements() -> bool:
    print_section("3. 检查 requirements.txt")

    path = PROJECT_ROOT / "requirements.txt"
    if not path.exists():
        print("❌ 缺少 requirements.txt")
        return False

    lines = path.read_text(
        encoding="utf-8",
        errors="ignore",
    ).splitlines()

    installed_requirements = set()

    for line in lines:
        package_name = normalize_requirement_line(line)
        if package_name:
            installed_requirements.add(package_name)

    print(f"当前识别到的依赖：{sorted(installed_requirements)}")

    ok = True

    for package in REQUIRED_REQUIREMENTS:
        package_name = normalize_requirement_line(package)

        if package_name in installed_requirements:
            print(f"✅ {package}")
        else:
            print(f"❌ requirements.txt 缺少依赖：{package}")
            ok = False

    return ok


def check_gitignore() -> bool:
    print_section("4. 检查 .gitignore")

    path = PROJECT_ROOT / ".gitignore"
    if not path.exists():
        print("❌ 缺少 .gitignore")
        return False

    content = path.read_text(encoding="utf-8", errors="ignore")

    ok = True

    for pattern in REQUIRED_GITIGNORE_PATTERNS:
        if pattern in content:
            print(f"✅ {pattern}")
        else:
            print(f"❌ .gitignore 缺少规则：{pattern}")
            ok = False

    if ".env.example" in content:
        print("⚠️  注意：.gitignore 中出现 .env.example，请确认没有把模板文件忽略掉")
    else:
        print("✅ .env.example 没有被忽略")

    return ok


def run_pytest() -> bool:
    print_section("5. 运行 pytest 单元测试")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )

    print(result.stdout)

    if result.stderr:
        print(result.stderr)

    if result.returncode == 0:
        print("✅ pytest 通过")
        return True

    print("❌ pytest 失败")
    return False


def main():
    print("=" * 80)
    print("Mini Coding Agent Backend 项目自检")
    print("=" * 80)
    print(f"项目根目录：{PROJECT_ROOT}")

    checks = [
        check_required_files(),
        check_env_files(),
        check_requirements(),
        check_gitignore(),
        run_pytest(),
    ]

    print_section("自检结果")

    passed = sum(1 for item in checks if item)
    total = len(checks)

    print(f"通过项：{passed}/{total}")

    if all(checks):
        print("✅ 项目自检通过，可以继续开发或准备提交。")
        sys.exit(0)

    print("❌ 项目自检未完全通过，请根据上面的提示修复。")
    sys.exit(1)


if __name__ == "__main__":
    main()