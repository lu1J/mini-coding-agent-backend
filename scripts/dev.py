import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_command(command: list[str]) -> int:
    """
    在项目根目录执行命令。
    """
    print()
    print("=" * 80)
    print("执行命令：")
    print(" ".join(command))
    print("=" * 80)

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        shell=False,
        check=False,
    )

    return result.returncode


def serve() -> int:
    """
    启动 FastAPI 开发服务。
    """
    return run_command([
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--reload",
    ])


def test() -> int:
    """
    运行 pytest 单元测试。
    """
    return run_command([
        sys.executable,
        "-m",
        "pytest",
        "tests/",
    ])


def eval_agent(eval_args: list[str] | None = None) -> int:
    """
    运行 Agent Eval。

    - 不带子命令：legacy 浅层评测入口（scripts/run_eval.py + eval_tasks.json，
      仅做兼容保留，不再演进）；
    - 带子命令：Day19 Agent Eval 框架（evals/ package）：
        eval smoke     运行冒烟任务（3 个固定任务，read/search/modify_approval）
        eval full      运行全部 16 个 golden 任务
        eval offline   离线重评分（位置参数 <id 或归档路径>）
        eval compare   版本对比（--baseline X --current Y）
        eval cleanup   清理隔离 workspace（--dry-run / --force / --run <id>）
      注意：smoke / full 运行前需要先启动后端服务。
    """
    eval_args = eval_args or []
    if not eval_args:
        print()
        print("[legacy] 运行 run_eval.py（浅层 eval，兼容保留；新评测请使用：eval smoke / full）")
        print("=" * 80)
        return run_command([
            sys.executable,
            "run_eval.py",
        ])
    subcommand = eval_args[0]
    if subcommand not in {"smoke", "full", "offline", "compare", "cleanup"}:
        print(f"未知 eval 子命令：{subcommand}")
        print("可用子命令：smoke / full / offline / compare / cleanup")
        print("不带子命令则运行 legacy run_eval.py")
        return 2
    return run_command([
        sys.executable,
        "-m",
        "evals.runner",
        *eval_args,
    ])


def check() -> int:
    """
    运行项目自检脚本。
    """
    return run_command([
        sys.executable,
        "scripts/check_project.py",
    ])


def release_check() -> int:
    """
    运行 GitHub 发布前安全检查。
    """
    return run_command([
        sys.executable,
        "scripts/check_release.py",
    ])


def health() -> int:
    """
    检查后端服务是否正常。
    """
    url = "http://127.0.0.1:8000/health"

    print()
    print("=" * 80)
    print(f"检查服务健康状态：{url}")
    print("=" * 80)

    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8", errors="ignore")
            print(f"HTTP 状态码：{response.status}")
            print(f"响应内容：{body}")

            if response.status == 200:
                print("✅ 服务运行正常")
                return 0

            print("❌ 服务响应异常")
            return 1

    except Exception as e:
        print("❌ 服务不可用")
        print(f"错误信息：{e}")
        print()
        print("请先启动服务：")
        print("python scripts/dev.py serve")
        return 1


def setup_demo() -> int:
    """
    初始化 demo_project 工作区。
    """
    return run_command([
        sys.executable,
        "scripts/setup_demo_workspace.py",
    ])


def main():
    parser = argparse.ArgumentParser(
        description="Mini Coding Agent Backend 开发命令入口"
    )

    parser.add_argument(
        "command",
        choices=[
            "serve",
            "test",
            "eval",
            "check",
            "health",
            "setup-demo",
            "release-check",
        ],
        help="要执行的开发命令",
    )

    # 只让 argparse 解析第一个 token（command 的 choices/help 校验）；
    # 其余参数原样透传给子命令（eval smoke/full/offline 等有自己的
    # argparse），避免全局 option 与子命令 option 互相干扰。
    argv = sys.argv[1:]
    args = parser.parse_args(argv[:1] if argv else None)
    command_args = argv[1:]

    if args.command == "serve":
        exit_code = serve()
    elif args.command == "test":
        exit_code = test()
    elif args.command == "eval":
        exit_code = eval_agent(command_args)
    elif args.command == "check":
        exit_code = check()
    elif args.command == "health":
        exit_code = health()
    elif args.command == "setup-demo":
        exit_code = setup_demo()
    elif args.command == "release-check":
        exit_code = release_check()
    else:
        print(f"未知命令：{args.command}")
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()