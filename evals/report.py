"""汇总、终端报告、结果归档与离线重评分。

归档位置：workspace/.agent_runs/evals/<eval_run_id>.json

离线重评分（offline）约束：
- 不调用 DeepSeek；
- 不发送 HTTP；
- 不读取/写入当前 workspace；
- 全部事实来自归档（task_snapshot + trace + evidence）。
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from evals.dataset import (
    EVAL_ARCHIVE_RELATIVE,
    PROJECT_ROOT,
    dataset_sha256,
)
from evals import EVALS_FRAMEWORK_VERSION

ARCHIVE_DIR = PROJECT_ROOT / EVAL_ARCHIVE_RELATIVE


# ------------------------------------------------------------------ git / meta


def _git_commit() -> str | None:
    try:
        output = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if output.returncode == 0:
            return output.stdout.strip()
    except Exception:  # noqa: BLE001 - git 不可用时返回 None
        pass
    return None


def _git_dirty() -> bool | None:
    try:
        output = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if output.returncode == 0:
            return bool(output.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return None


def build_meta(*, eval_run_id: str, mode: str) -> dict[str, Any]:
    """归档 meta。绝不包含 API key / Authorization / .env 内容。"""
    return {
        "eval_run_id": eval_run_id,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "framework_version": EVALS_FRAMEWORK_VERSION,
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "model_name": os.environ.get("DEEPSEEK_MODEL") or "unknown",
        "tasks_sha256": dataset_sha256(),
    }


def build_archive(
    *,
    eval_run_id: str,
    mode: str,
    results: list[dict[str, Any]],
    base_url: str | None = None,
) -> dict[str, Any]:
    """组装归档字典（只挑选安全字段，绝不包含请求头/密钥）。"""
    _ = base_url  # 预留：未来如需记录后端地址版本
    meta = build_meta(eval_run_id=eval_run_id, mode=mode)
    tasks = []
    for result in results:
        tasks.append(
            {
                "task_id": result.get("task_id"),
                "name": result.get("name"),
                "category": result.get("category"),
                "smoke": bool(result.get("smoke")),
                "prompt": result.get("prompt"),
                "duration_ms": result.get("duration_ms"),
                "wall_seconds": result.get("wall_seconds"),
                "task_snapshot": result.get("task_snapshot"),
                "trace": result.get("trace"),
                "evidence": result.get("evidence"),
                "metrics": result.get("metrics"),
                "checks": result.get("checks"),
                "errors": result.get("errors"),
                "passed": bool(result.get("passed")),
                "verdict": result.get("verdict"),
            }
        )
    return {"meta": meta, "tasks": tasks}


def archive_path_for(eval_run_id: str) -> Path:
    return ARCHIVE_DIR / f"{eval_run_id}.json"


def write_archive(eval_run_id: str, archive: dict[str, Any]) -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = archive_path_for(eval_run_id)
    path.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def list_archive_ids() -> list[str]:
    if not ARCHIVE_DIR.exists():
        return []
    return sorted(path.stem for path in ARCHIVE_DIR.glob("*.json"))


def resolve_archive(identifier: str) -> Path:
    """把 eval_run_id / 文件名 / 任意路径解析为归档文件。"""
    candidate = Path(identifier)
    if candidate.suffix == ".json" or candidate.parent != Path("."):
        if candidate.exists():
            return candidate
        if candidate.suffix == ".json":
            nested = ARCHIVE_DIR / candidate.name
            if nested.exists():
                return nested
        raise FileNotFoundError(
            f"找不到归档：{identifier}（可用：{list_archive_ids() or '无'}）"
        )
    path = archive_path_for(identifier)
    if not path.exists():
        raise FileNotFoundError(
            f"找不到归档：{identifier}（可用：{list_archive_ids() or '无'}）"
        )
    return path


# ------------------------------------------------------------------ 运行前检查


def check_server_health(base_url: str) -> dict[str, Any]:
    try:
        import requests

        response = requests.get(base_url.rstrip("/") + "/health", timeout=10)
        if response.status_code == 200:
            return {"ok": True}
        return {"ok": False, "error": f"HTTP {response.status_code}"}
    except Exception as error:  # noqa: BLE001 - 网络问题统一转错误信息
        return {"ok": False, "error": str(error)}


# ------------------------------------------------------------------ 终端展示


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _one_line(result: dict[str, Any]) -> str:
    metrics = result.get("metrics") or {}
    verdict = "PASS" if result.get("passed") else "FAIL"
    tag = f"[{verdict}]"
    status = _fmt(metrics.get("final_status"))
    tool_calls = _fmt(metrics.get("tool_call_count"))
    plan = _fmt(metrics.get("plan_completion"))
    return (
        f"{tag} {result.get('task_id')}  status={status} "
        f"plan={plan} tools={tool_calls} "
        f"guardrail={_fmt(metrics.get('guardrail_interventions'))} "
        f"protocol={_fmt(metrics.get('protocol_errors'))} "
        f"duration={_fmt(metrics.get('duration_ms'))}ms"
    )


def _metric_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    declared_required = [
        r for r in results
        if r.get("metrics") and r["metrics"].get("required_tools_satisfied") is not None
    ]
    declared_forbidden = [
        r for r in results
        if r.get("metrics") and r["metrics"].get("forbidden_tools_avoided") is not None
    ]
    declared_approval = [
        r for r in results
        if r.get("metrics") and r["metrics"].get("approval_correctness") is not None
    ]
    plan_values = [
        r["metrics"]["plan_completion"]
        for r in results
        if r.get("metrics") and r["metrics"].get("plan_completion") is not None
    ]
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "required_rate": (
            sum(1 for r in declared_required if r["metrics"]["required_tools_satisfied"])
            / len(declared_required)
            if declared_required
            else None
        ),
        "forbidden_rate": (
            sum(1 for r in declared_forbidden if r["metrics"]["forbidden_tools_avoided"])
            / len(declared_forbidden)
            if declared_forbidden
            else None
        ),
        "approval_rate": (
            sum(1 for r in declared_approval if r["metrics"]["approval_correctness"])
            / len(declared_approval)
            if declared_approval
            else None
        ),
        "plan_avg": (sum(plan_values) / len(plan_values)) if plan_values else None,
        "protocol_total": sum(
            (r.get("metrics") or {}).get("protocol_errors") or 0 for r in results
        ),
        "guardrail_total": sum(
            (r.get("metrics") or {}).get("guardrail_interventions") or 0 for r in results
        ),
        "premature_total": sum(
            (r.get("metrics") or {}).get("premature_final_count") or 0 for r in results
        ),
        "stalled_total": sum(
            1 for r in results if (r.get("metrics") or {}).get("executor_stalled")
        ),
        "tool_calls_total": sum(
            (r.get("metrics") or {}).get("tool_call_count") or 0 for r in results
        ),
        "duration_avg_ms": (
            sum((r.get("metrics") or {}).get("duration_ms") or 0 for r in results)
            / len(results)
            if results
            else None
        ),
        "status_counts": {},
    }


def print_run_summary(results: list[dict[str, Any]]) -> None:
    summary = _metric_summary(results)
    print()
    print("=== 逐任务结果 ===")
    for result in results:
        print("  " + _one_line(result))
        if not result.get("passed"):
            for error in result.get("errors") or []:
                print(f"      ✗ {error}")
        else:
            for check in result.get("checks") or []:
                if not check.get("required") and not check.get("passed"):
                    print(f"      ⚠ {check['label']}：{check['detail']}")

    print()
    print("=== 汇总（12 项指标）===")
    status_counts: dict[str, int] = {}
    for result in results:
        status = (result.get("metrics") or {}).get("final_status") or "无响应"
        status_counts[status] = status_counts.get(status, 0) + 1
    lines = [
        ("通过率（A）", f"{summary['passed']}/{summary['total']}"),
        ("必须工具满足率（B）", _fmt(summary["required_rate"])),
        ("禁止工具规避率（C）", _fmt(summary["forbidden_rate"])),
        ("计划完成率均值（D）", _fmt(summary["plan_avg"])),
        ("审批正确率（E）", _fmt(summary["approval_rate"])),
        ("协议错误总数（F）", summary["protocol_total"]),
        ("护栏介入总数（G）", summary["guardrail_total"]),
        ("提前结束总数（H）", summary["premature_total"]),
        ("执行器卡住任务数（I）", summary["stalled_total"]),
        ("工具调用总数（J）", summary["tool_calls_total"]),
        ("任务平均耗时 ms（K）", _fmt(summary["duration_avg_ms"])),
        ("最终状态分布（L）", "、".join(f"{k}={v}" for k, v in sorted(status_counts.items()))),
    ]
    width = max(len(label) for label, _ in lines)
    for label, value in lines:
        print(f"  {label:<{width}}  {value}")


# ------------------------------------------------------------------ 离线重评分


def offline_rescore(identifier: str) -> dict[str, Any]:
    """从归档离线重评分并打印中文报告。全程无模型 / HTTP / workspace。"""
    from evals.scoring import score_task

    path = resolve_archive(identifier)
    archive = json.loads(path.read_text(encoding="utf-8"))
    meta = archive.get("meta") or {}
    stored_tasks = archive.get("tasks") or []

    print("=== Agent Eval 离线重评分 ===")
    print(f"归档：{path}")
    print(f"meta：mode={meta.get('mode')} 时间={meta.get('timestamp')}")
    print(
        f"  git_commit={meta.get('git_commit')} git_dirty={meta.get('git_dirty')} "
        f"model={meta.get('model_name')}"
    )
    current_sha = dataset_sha256()
    if meta.get("tasks_sha256") == current_sha:
        print(f"任务集 sha256 一致：{current_sha[:16]}…")
        sha_ok = True
    else:
        print(
            f"⚠ 任务集 sha256 不一致！归档={str(meta.get('tasks_sha256'))[:16]}… "
            f"当前={current_sha[:16]}…（本次重评分仍使用归档 task_snapshot）"
        )
        sha_ok = False

    total = len(stored_tasks)
    archived_passed = sum(1 for task in stored_tasks if task.get("passed"))
    rescored_passed = 0
    mismatches: list[dict[str, Any]] = []

    for task in stored_tasks:
        task_id = task.get("task_id")
        try:
            scored = score_task(
                task=task.get("task_snapshot") or {},
                trace=task.get("trace") or {},
                evidence=task.get("evidence") or {},
                duration_ms=task.get("duration_ms"),
            )
        except Exception as error:  # noqa: BLE001 - 单任务异常不中断
            scored = {
                "passed": False,
                "verdict": "failed",
                "errors": [f"离线重评分异常：{error!r}"],
                "metrics": {},
                "checks": [],
            }
        rescored_ok = bool(scored.get("passed"))
        if rescored_ok:
            rescored_passed += 1
        archived_ok = bool(task.get("passed"))
        if archived_ok != rescored_ok:
            direction = "PASS → FAIL" if archived_ok else "FAIL → PASS"
            mismatch = {
                "task_id": task_id,
                "direction": direction,
                "archived_errors": (task.get("errors") or [])[:3],
                "rescored_errors": (scored.get("errors") or [])[:3],
            }
            mismatches.append(mismatch)
            print(
                f"  ✗ 判定不一致 [{task_id}]：归档={task.get('verdict')} "
                f"重评分={scored.get('verdict')}"
            )
            print(f"      归档原因：{'；'.join(mismatch['archived_errors']) or '无'}")
            print(f"      重评分原因：{'；'.join(mismatch['rescored_errors']) or '无'}")
        else:
            mark = "PASS" if rescored_ok else "FAIL"
            print(f"  [{mark}] {task_id}")

    print()
    print(
        f"任务 {total} 个；归档通过 {archived_passed}，"
        f"离线重评分通过 {rescored_passed}；判定不一致 {len(mismatches)} 个。"
    )
    if sha_ok and rescored_passed == total and archived_passed == total and not mismatches:
        print("结果：全部通过且重评分与归档一致。")
    return {
        "archive": str(path),
        "mode": meta.get("mode"),
        "timestamp": meta.get("timestamp"),
        "total": total,
        "passed": rescored_passed,
        "archived_passed": archived_passed,
        "mismatch_count": len(mismatches),
        "tasks_sha256_ok": sha_ok,
        "mismatches": mismatches,
    }
