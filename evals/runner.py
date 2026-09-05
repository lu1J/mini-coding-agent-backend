"""Agent Eval 执行器（真实任务运行）。

职责：
- 为每个任务创建隔离 fixture 副本（workspace/eval_cases/<run_id>/<task_id>/demo_project）；
- 通过 HTTP 调起真实 LangGraph v2 Agent（/agent/code/graph/v2）；
- 审批流：waiting_approval → 文件证据取证 → resume（approved/rejected）→ 循环；
- 保存原始响应 trace 与文件证据（before / approval / after）；
- 调用 scoring.score_task 得出确定性评分；
- CLI 子命令：smoke / full / offline / compare / cleanup。

约束：
- 绝不修改 / 删除日常 workspace/demo_project（只操作 workspace/eval_cases/…）；
- 保存内容绝不含 API key / Authorization / .env 内容（本模块从不读取这些）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from evals.dataset import (
    CASE_ROOT_RELATIVE,
    PROJECT_ROOT,
    case_dirs,
    copy_fixture_into_case,
    fixture_files_exist,
    load_golden_tasks,
)

# 服务器地址与路由（与 run_eval.py 一致）。
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
INVOKE_PATH = "/agent/code/graph/v2"
RESUME_PATH_TEMPLATE = "/agent/code/graph/v2/{thread_id}/resume"

# 单个 HTTP 调用的超时（Agent 一轮可能跑很久）。
HTTP_TIMEOUT_SECONDS = 1800
HTTP_MAX_ATTEMPTS = 3

# 单任务最多经历几次审批中断（数据集内最多 1~2 次）。
MAX_APPROVAL_ROUNDS = 6

# 证据快照排除项：运行期产物（不是“评测对象”）。
EXCLUDE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".git"}
EXCLUDE_FILE_SUFFIXES = {".pyc", ".pyo"}

APPROVAL_DECISION = {"approve": True, "reject": False, "none": None}


# ------------------------------------------------------------------ 证据取证


def _normalize_text(raw: bytes) -> str:
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def snapshot_tree(root: Path) -> dict[str, Any]:
    """对目录树做逐文件快照（相对路径 → {exists, sha256, content}）。

    content 仅对 UTF-8 文本记录（规范化换行）；二进制为 None。
    """
    snapshot: dict[str, Any] = {}
    if not root.exists():
        return snapshot
    for file in sorted(root.rglob("*")):
        if not file.is_file():
            continue
        if file.suffix.lower() in EXCLUDE_FILE_SUFFIXES:
            continue
        parts = set(file.relative_to(root).parts)
        if parts & EXCLUDE_DIR_NAMES:
            continue
        rel = file.relative_to(root).as_posix()
        raw = file.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        content: str | None
        try:
            content = _normalize_text(raw)
        except UnicodeDecodeError:
            content = None
        snapshot[rel] = {"exists": True, "sha256": digest, "content": content}
    return snapshot


# ------------------------------------------------------------------ HTTP


def _record_http_error(
    trace: dict[str, Any],
    *,
    phase: str,
    attempt: int,
    message: str,
    status_code: int | None = None,
    body: str | None = None,
) -> None:
    trace.setdefault("http_errors", []).append(
        {
            "phase": phase,
            "attempt": attempt,
            "status_code": status_code,
            "body": (body or "")[:500],
            "message": str(message)[:300],
        }
    )


def _post_json(
    trace: dict[str, Any],
    *,
    phase: str,
    url: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    import requests  # 延迟导入，offline 场景不依赖 requests

    for attempt in range(1, HTTP_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(url, json=payload, timeout=HTTP_TIMEOUT_SECONDS)
        except requests.exceptions.ConnectionError as error:
            _record_http_error(
                trace, phase=phase, attempt=attempt,
                message=f"连接失败：{error}",
            )
        except requests.exceptions.Timeout as error:
            _record_http_error(
                trace, phase=phase, attempt=attempt, message=f"超时：{error}",
            )
        except Exception as error:  # noqa: BLE001 - 兜底记录
            _record_http_error(
                trace, phase=phase, attempt=attempt, message=f"请求异常：{error}",
            )
        else:
            if response.status_code != 200:
                _record_http_error(
                    trace,
                    phase=phase,
                    attempt=attempt,
                    status_code=response.status_code,
                    body=response.text,
                    message=f"HTTP {response.status_code}",
                )
            else:
                try:
                    return response.json()
                except ValueError as error:
                    _record_http_error(
                        trace,
                        phase=phase,
                        attempt=attempt,
                        status_code=200,
                        body=response.text,
                        message=f"响应不是 JSON：{error}",
                    )
        time.sleep(2 * attempt)
    return None


def run_agent_session(
    *,
    base_url: str,
    prompt: str,
    max_steps: int,
    approval_flow: str,
    on_approval=None,
) -> dict[str, Any]:
    """执行一个任务的完整 Agent 会话，返回 trace。

    trace 形如：
        {"invoke": <响应|None>, "resumes": [<响应>, ...],
         "http_errors": [...], "notes": [...]}

    on_approval(pending_action)：每次进入 waiting_approval 时（在 resume
    之前）被调用，用于在“审批前零副作用”现场完成文件取证。
    """
    base = base_url.rstrip("/")
    trace: dict[str, Any] = {"invoke": None, "resumes": [], "http_errors": [], "notes": []}
    decision = APPROVAL_DECISION.get(approval_flow)
    if approval_flow != "none" and decision is None:
        trace["notes"].append(f"未知审批流：{approval_flow}（当作无审批处理）")

    response = _post_json(
        trace,
        phase="invoke",
        url=f"{base}{INVOKE_PATH}",
        payload={"message": prompt, "max_steps": max_steps},
    )
    trace["invoke"] = response

    approval_rounds = 0
    while response is not None and response.get("status") == "waiting_approval":
        pending = response.get("pending_action") or {}
        if on_approval is not None:
            on_approval(pending)
        thread_id = response.get("thread_id")
        if not thread_id:
            trace["notes"].append("waiting_approval 响应缺少 thread_id，无法恢复")
            break
        if decision is None:
            trace["notes"].append("任务不期望审批但 Agent 进入 waiting_approval，停止恢复")
            break
        approval_rounds += 1
        if approval_rounds >= MAX_APPROVAL_ROUNDS:
            trace["notes"].append(f"审批中断超过 {MAX_APPROVAL_ROUNDS} 轮，停止恢复")
            break
        response = _post_json(
            trace,
            phase="resume",
            url=f"{base}{RESUME_PATH_TEMPLATE.format(thread_id=thread_id)}",
            payload={"approved": decision},
        )
        trace["resumes"].append(response)
    return trace


# ------------------------------------------------------------------ 单任务执行


def run_one_task(
    *,
    task: dict[str, Any],
    eval_run_id: str,
    base_url: str,
) -> dict[str, Any]:
    task_id = str(task.get("id") or "unknown")
    case_abs, case_rel = case_dirs(eval_run_id, task_id)
    copy_fixture_into_case(case_abs)
    prompt = str(task["prompt_template"]).format(case_root=case_rel)

    evidence_before = snapshot_tree(case_abs)
    evidence: dict[str, Any] = {
        "before": evidence_before,
        "approval": {},
        "after": {},
        "approval_events": [],
    }

    def _on_approval(pending: dict[str, Any]) -> None:
        # 审批前零副作用取证：首个中断时快照文件树并记录待审批动作。
        if not evidence["approval"]:
            evidence["approval"] = snapshot_tree(case_abs)
        evidence["approval_events"].append(
            {
                "index": len(evidence["approval_events"]),
                "pending_tool_name": pending.get("tool_name"),
                "tool_args": pending.get("tool_args"),
            }
        )

    started = time.perf_counter()
    trace = run_agent_session(
        base_url=base_url,
        prompt=prompt,
        max_steps=int(task.get("max_steps") or 8),
        approval_flow=(task.get("approval") or {}).get("flow", "none"),
        on_approval=_on_approval,
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 1)

    evidence["after"] = snapshot_tree(case_abs)

    return {
        "task_id": task_id,
        "name": task.get("name"),
        "category": task.get("category"),
        "smoke": bool(task.get("smoke")),
        "prompt": prompt,
        "duration_ms": duration_ms,
        "trace": trace,
        "evidence": evidence,
        "task_snapshot": task,
    }


def run_tasks(
    *,
    eval_run_id: str,
    tasks: list[dict[str, Any]],
    base_url: str,
    on_task_done=None,
) -> list[dict[str, Any]]:
    """串行执行多个任务并即时评分。"""
    from evals.scoring import score_task  # 延迟导入保持轻量

    results: list[dict[str, Any]] = []
    total = len(tasks)
    for index, task in enumerate(tasks, start=1):
        task_id = task.get("id")
        print(f"[{index}/{total}] 运行任务 {task_id}（{task.get('name')}）…", flush=True)
        started = time.perf_counter()
        raw = run_one_task(
            task=task,
            eval_run_id=eval_run_id,
            base_url=base_url,
        )
        raw["wall_seconds"] = round(time.perf_counter() - started, 1)
        try:
            scored = score_task(
                task=task,
                trace=raw["trace"],
                evidence=raw["evidence"],
                duration_ms=raw["duration_ms"],
            )
        except Exception as error:  # noqa: BLE001 - 评分器异常不中断整个 run
            scored = {
                "task_id": task_id,
                "metrics": {},
                "checks": [],
                "errors": [f"评分异常：{error!r}"],
                "passed": False,
                "verdict": "failed",
            }
        raw.update(scored)
        results.append(raw)
        if on_task_done:
            on_task_done(raw)
    return results


# ------------------------------------------------------------------ 目录管理


def list_case_run_ids() -> list[str]:
    case_root = PROJECT_ROOT / CASE_ROOT_RELATIVE
    if not case_root.exists():
        return []
    return sorted(
        entry.name for entry in case_root.iterdir() if entry.is_dir()
    )


def cleanup_cases(
    *,
    run_id: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """清理隔离 case 目录。

    - 指定 --run：只清理该 run_id；
    - 未指定：清理所有“已有归档”的 run（数据已落盘，安全）；
      无归档的 run 需 --force 才清理。
    """
    from evals.report import archive_path_for

    case_root = PROJECT_ROOT / CASE_ROOT_RELATIVE
    run_ids = list_case_run_ids()
    if run_id is not None:
        run_ids = [rid for rid in run_ids if rid == run_id]
    if not run_ids:
        return {"removed": [], "kept": [], "refused": []}

    removed: list[str] = []
    kept: list[str] = []
    refused: list[str] = []
    for rid in run_ids:
        target = case_root / rid
        has_archive = archive_path_for(rid).exists()
        if not has_archive and not force:
            refused.append(rid)
            continue
        if dry_run:
            kept.append(rid)
            continue
        import shutil

        shutil.rmtree(target, ignore_errors=True)
        removed.append(rid)
    return {"removed": removed, "kept": kept, "refused": refused}


# ------------------------------------------------------------------ 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.runner",
        description="Agent Eval：真实任务执行 / 离线重评分 / 结果对比。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("smoke", "full"):
        p = sub.add_parser(name, help=f"运行{'冒烟(3 个)' if name == 'smoke' else '全部 16 个'}任务")
        p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="后端地址")
        p.add_argument("--only", nargs="*", default=None, help="只运行指定任务 id（可多个）")

    p = sub.add_parser("offline", help="离线重评分（不访问模型 / HTTP / 当前 workspace）")
    p.add_argument("run", help="eval_run_id 或归档文件路径")
    p.add_argument("--json", action="store_true", help="额外输出 JSON 摘要")

    p = sub.add_parser("compare", help="对比两个 eval run（回归检测）")
    p.add_argument("--baseline", required=True, help="基线：eval_run_id 或归档路径")
    p.add_argument("--current", required=True, help="当前：eval_run_id 或归档路径")

    p = sub.add_parser("cleanup", help="清理隔离 case 目录")
    p.add_argument("--run", default=None, help="只清理指定 run_id")
    p.add_argument("--force", action="store_true", help="无归档的 run 也清理")
    p.add_argument("--dry-run", action="store_true", help="只打印将清理什么")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command in ("smoke", "full"):
        return _cmd_run(args)
    if args.command == "offline":
        from evals.report import offline_rescore

        summary = offline_rescore(args.run)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["passed"] == summary["total"] else 1
    if args.command == "compare":
        from evals.compare import compare_runs

        return compare_runs(args.baseline, args.current)
    if args.command == "cleanup":
        result = cleanup_cases(
            run_id=args.run,
            force=args.force,
            dry_run=args.dry_run,
        )
        print(f"已清理：{result['removed'] or '（无）'}")
        print(f"保留：{result['kept'] or '（无）'}")
        if result["refused"]:
            print(f"拒绝清理（无归档，需 --force）：{result['refused']}")
        return 0
    print(f"未知子命令：{args.command}")
    return 2


def _cmd_run(args: argparse.Namespace) -> int:
    from evals import EVALS_FRAMEWORK_VERSION
    from evals.dataset import dataset_sha256, load_golden_tasks, validate_dataset
    from evals.report import (
        build_archive,
        print_run_summary,
        write_archive,
    )

    if args.command == "smoke":
        mode = "smoke"
        tasks = [task for task in load_golden_tasks() if task.get("smoke")]
    else:
        mode = "full"
        tasks = load_golden_tasks()

    problems = validate_dataset([task for task in load_golden_tasks()])
    if problems:
        print("任务集校验发现以下问题（继续执行）：")
        for problem in problems:
            print(f"  - {problem}")

    missing = fixture_files_exist()
    if missing:
        print(f"fixture 缺失文件：{missing}")
        return 2

    if args.only:
        only = set(args.only)
        tasks = [task for task in tasks if task.get("id") in only]
        missing_ids = sorted(only - {task.get("id") for task in tasks})
        if missing_ids:
            print(f"警告：--only 指定的任务不存在：{missing_ids}")

    if not tasks:
        print("没有可运行的任务。")
        return 2

    print(f"=== Agent Eval {mode}（框架版本 {EVALS_FRAMEWORK_VERSION}）===")
    print(f"任务数量：{len(tasks)}（共 {len(load_golden_tasks())} 个任务的评测集）")
    print(f"后端地址：{args.base_url}")

    from evals.report import check_server_health

    health = check_server_health(args.base_url)
    if not health["ok"]:
        print(f"后端健康检查失败：{health.get('error')}")
        print("请先启动后端（uvicorn main:app），再运行评测。")
        return 2

    eval_run_id = time.strftime("eval_%Y%m%d_%H%M%S")
    print(f"评测 run_id：{eval_run_id}")
    print(f"工作区隔离目录：{CASE_ROOT_RELATIVE}/{eval_run_id}")

    def _on_done(result: dict[str, Any]) -> None:
        status = "PASS" if result.get("passed") else "FAIL"
        reason = "；".join(result.get("errors") or []) or "（无失败项）"
        print(f"  -> [{status}] {result['task_id']}：{reason}")

    results = run_tasks(
        eval_run_id=eval_run_id,
        tasks=tasks,
        base_url=args.base_url,
        on_task_done=_on_done,
    )
    passed = sum(1 for result in results if result.get("passed"))
    print()
    print(f"=== 运行完成：{passed}/{len(results)} 通过 ===")

    archive = build_archive(
        eval_run_id=eval_run_id,
        mode=mode,
        results=results,
        base_url=args.base_url,
    )
    archive_path = write_archive(eval_run_id, archive)
    print(f"归档：{archive_path}")
    print_run_summary(results)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
