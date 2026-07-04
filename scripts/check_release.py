import argparse
import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


FORBIDDEN_TRACKED_PATHS = {
    ".env",
    "eval_result.json",
    "body_stream.json",
    "test_deepseek.py",
    "test_tool_calling.py",
}


FORBIDDEN_TRACKED_PREFIXES = [
    ".venv/",
    "venv/",
    "workspace/.agent_runs/",
    "workspace/.agent_pending/",
    "workspace/demo_project/",
    "workspace/test_project",
    "workspace/test_git_project",
    "workspace/test_not_git_project",
]


SKIP_SECRET_SCAN_PREFIXES = [
    "docs/",
]


SKIP_SECRET_SCAN_FILES = {
    "README.md",
    ".env.example",
    "scripts/check_release.py",
}


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
]


def print_section(title: str):
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def run_git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )


def normalize_git_path(path: str) -> str:
    return path.replace("\\", "/")


def is_git_repo() -> bool:
    result = run_git(["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def get_tracked_files() -> list[str]:
    result = run_git(["ls-files"])
    if result.returncode != 0:
        return []

    return [
        normalize_git_path(line.strip())
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def check_working_tree_clean(allow_dirty: bool) -> bool:
    print_section("1. 检查 Git 工作区状态")

    result = run_git(["status", "--short"])

    if result.returncode != 0:
        print("❌ 无法获取 Git 状态")
        print(result.stderr)
        return False

    status = result.stdout.strip()

    if not status:
        print("✅ Git 工作区干净")
        return True

    print("⚠️  当前工作区存在未提交修改：")
    print(status)

    if allow_dirty:
        print("⚠️  当前使用 --allow-dirty，仅开发阶段允许通过。")
        return True

    print("❌ 发布前要求工作区干净，请先提交或处理这些修改。")
    return False


def check_forbidden_tracked_files(tracked_files: list[str]) -> bool:
    print_section("2. 检查禁止提交的文件")

    ok = True

    for file_path in tracked_files:
        if file_path in FORBIDDEN_TRACKED_PATHS:
            print(f"❌ 禁止提交文件已被 Git 跟踪：{file_path}")
            ok = False

        for prefix in FORBIDDEN_TRACKED_PREFIXES:
            if file_path.startswith(prefix):
                print(f"❌ 禁止提交目录中的文件已被 Git 跟踪：{file_path}")
                ok = False

    if ok:
        print("✅ 未发现禁止提交的文件被 Git 跟踪")

    return ok


def should_skip_secret_scan(file_path: str) -> bool:
    if file_path in SKIP_SECRET_SCAN_FILES:
        return True

    for prefix in SKIP_SECRET_SCAN_PREFIXES:
        if file_path.startswith(prefix):
            return True

    return False


def is_probably_text_file(path: Path) -> bool:
    text_suffixes = {
        ".py",
        ".md",
        ".txt",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".ini",
        ".env",
        ".example",
        ".gitignore",
        ".dockerignore",
    }

    if path.suffix.lower() in text_suffixes:
        return True

    if path.name in {"Dockerfile", ".gitignore", ".dockerignore"}:
        return True

    return False


def line_has_real_env_secret(line: str) -> bool:
    """
    检查真正像 .env 配置行的真实密钥。

    只检查这种形式：

    DEEPSEEK_API_KEY=xxx
    OPENAI_API_KEY=xxx
    ANTHROPIC_API_KEY=xxx

    不检查 Python 代码里的字符串，例如：
    "DEEPSEEK_API_KEY="
    """
    stripped = line.strip()

    if not stripped:
        return False

    if stripped.startswith("#"):
        return False

    match = re.match(
        r"^(DEEPSEEK_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*=\s*(.+)$",
        stripped,
        re.IGNORECASE,
    )

    if not match:
        return False

    value = match.group(2).strip().strip('"').strip("'")

    if not value:
        return False

    safe_words = [
        "YOUR_",
        "PLACEHOLDER",
        "EXAMPLE",
        "HERE",
        "FAKE",
        "TEST",
        "DUMMY",
    ]

    upper_value = value.upper()

    if any(word in upper_value for word in safe_words):
        return False

    return True

    upper_value = value.upper()

    if any(word in upper_value for word in safe_words):
        return False

    return True


def check_possible_secrets(tracked_files: list[str]) -> bool:
    print_section("3. 扫描疑似密钥")

    ok = True

    for file_path in tracked_files:
        normalized_path = normalize_git_path(file_path)

        if should_skip_secret_scan(normalized_path):
            continue

        path = PROJECT_ROOT / normalized_path

        if not path.exists() or not path.is_file():
            continue

        if not is_probably_text_file(path):
            continue

        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue

        for line_number, line in enumerate(lines, start=1):
            if line_has_real_env_secret(line):
                print(f"❌ 疑似真实 API Key：{normalized_path}:{line_number}")
                ok = False

            for pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    print(f"❌ 疑似密钥模式：{normalized_path}:{line_number}")
                    ok = False

    if ok:
        print("✅ 未发现明显疑似密钥")

    return ok


def main():
    parser = argparse.ArgumentParser(
        description="GitHub 发布前安全检查"
    )

    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="允许工作区存在未提交修改，仅用于开发阶段测试脚本",
    )

    args = parser.parse_args()

    print("=" * 80)
    print("Mini Coding Agent Backend 发布前安全检查")
    print("=" * 80)
    print(f"项目根目录：{PROJECT_ROOT}")

    if not is_git_repo():
        print("❌ 当前目录不是 Git 仓库，请先 git init。")
        sys.exit(1)

    tracked_files = get_tracked_files()

    checks = [
        check_working_tree_clean(allow_dirty=args.allow_dirty),
        check_forbidden_tracked_files(tracked_files),
        check_possible_secrets(tracked_files),
    ]

    print_section("发布前检查结果")

    if all(checks):
        print("✅ 发布前安全检查通过")
        sys.exit(0)

    print("❌ 发布前安全检查未通过，请根据上面的提示修复。")
    sys.exit(1)


if __name__ == "__main__":
    main()