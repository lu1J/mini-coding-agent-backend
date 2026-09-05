"""两个 eval run 的结果对比（回归检测）。

- 逐指标差异表（基线 vs 当前）；
- PASS → FAIL（回归）与 FAIL → PASS（改善）任务清单；
- 只在基线 / 只在当前的任务清单；
- tasks_sha256 不一致时警告。

纯读取：只访问归档文件，不调用模型 / HTTP / workspace。
"""

from __future__ import annotations

import json
from typing import Any

from evals.report import resolve_archive

# 展示顺序与说明。
METRIC_ROWS = [
    ("required_tools_satisfied", "必须工具满足率（B）", "rate"),
    ("forbidden_tools_avoided", "禁止工具规避率（C）", "rate"),
    ("plan_completion", "计划完成率均值（D）", "avg"),
    ("approval_correctness", "审批正确率（E）", "rate"),
    ("protocol_errors", "协议错误总数（F）", "sum"),
    ("guardrail_interventions", "护栏介入总数（G）", "sum"),
    ("premature_final_count", "提前结束总数（H）", "sum"),
    ("executor_stalled", "执行器卡住任务数（I）", "stall_sum"),
    ("tool_call_count", "工具调用总数（J）", "sum"),
    ("duration_ms", "任务平均耗时 ms（K）", "avg"),
]


def _load_archive(identifier: str) -> dict[str, Any]:
    path = resolve_archive(identifier)
    return json.loads(path.read_text(encoding="utf-8"))


def _aggregate(tasks: list[dict[str, Any]], metric: str, kind: str) -> float | None:
    if kind == "rate":
        declared = [t for t in tasks if (t.get("metrics") or {}).get(metric) is not None]
        if not declared:
            return None
        return sum(1 for t in declared if t["metrics"][metric]) / len(declared)
    if kind == "stall_sum":
        return float(sum(1 for t in tasks if (t.get("metrics") or {}).get(metric)))
    if kind == "avg":
        values = [
            (t.get("metrics") or {}).get(metric)
            for t in tasks
            if (t.get("metrics") or {}).get(metric) is not None
        ]
        if not values:
            return None
        return sum(values) / len(values)
    return float(sum((t.get("metrics") or {}).get(metric) or 0 for t in tasks))


def _fmt_rate(value: float | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def _fmt_num(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def compare_runs(baseline_id: str, current_id: str) -> int:
    """打印中文对比报告。返回 0 = 无回归；1 = 存在回归或当前有失败。"""
    baseline_archive = _load_archive(baseline_id)
    current_archive = _load_archive(current_id)
    baseline_meta = baseline_archive.get("meta") or {}
    current_meta = current_archive.get("meta") or {}
    baseline_tasks = {t["task_id"]: t for t in baseline_archive.get("tasks") or []}
    current_tasks = {t["task_id"]: t for t in current_archive.get("tasks") or []}
    common_ids = sorted(set(baseline_tasks) & set(current_tasks))
    only_baseline = sorted(set(baseline_tasks) - set(current_tasks))
    only_current = sorted(set(current_tasks) - set(baseline_tasks))

    print("=== Agent Eval 版本对比 ===")
    print(f"基线  ：{baseline_id}  mode={baseline_meta.get('mode')}  时间={baseline_meta.get('timestamp')}")
    print(f"当前  ：{current_id}  mode={current_meta.get('mode')}  时间={current_meta.get('timestamp')}")
    baseline_sha = baseline_meta.get("tasks_sha256")
    current_sha = current_meta.get("tasks_sha256")
    if baseline_sha and current_sha and baseline_sha != current_sha:
        print(
            f"⚠ 任务集 sha256 不一致：基线={str(baseline_sha)[:16]}… "
            f"当前={str(current_sha)[:16]}…（任务集版本不同，指标对比仅作参考）"
        )
    else:
        print(f"任务集 sha256 一致：{str(baseline_sha or current_sha)[:16]}…")
    print()

    # ---- 逐任务判定变化。
    regressions: list[str] = []
    improvements: list[str] = []
    for task_id in common_ids:
        base_pass = bool(baseline_tasks[task_id].get("passed"))
        current_pass = bool(current_tasks[task_id].get("passed"))
        if base_pass and not current_pass:
            regressions.append(task_id)
        elif not base_pass and current_pass:
            improvements.append(task_id)

    print(f"共同任务：{len(common_ids)}；仅在基线：{len(only_baseline)}；仅在当前：{len(only_current)}")
    if only_baseline:
        print(f"  仅在基线（当前缺失）：{only_baseline}")
    if only_current:
        print(f"  仅在当前（基线缺失）：{only_current}")
    print()

    print("=== PASS→FAIL（回归）===")
    if regressions:
        for task_id in regressions:
            current = current_tasks[task_id]
            reasons = "；".join(current.get("errors") or []) or "（无失败项）"
            print(f"  ✗ {task_id}：{reasons}")
    else:
        print("  （无）")
    print()
    print("=== FAIL→PASS（改善）===")
    if improvements:
        for task_id in improvements:
            print(f"  ✓ {task_id}")
    else:
        print("  （无）")
    print()

    # ---- 逐指标差异。
    print("=== 逐指标差异（共同任务）===")
    base_common = [baseline_tasks[tid] for tid in common_ids]
    current_common = [current_tasks[tid] for tid in common_ids]
    print(f"{'指标':<22}{'基线':>14}{'当前':>14}{'变化':>14}")
    for metric, label, kind in METRIC_ROWS:
        base_value = _aggregate(base_common, metric, kind)
        current_value = _aggregate(current_common, metric, kind)
        fmt = _fmt_rate if kind == "rate" else _fmt_num
        delta = ""
        if base_value is not None and current_value is not None:
            diff = current_value - base_value
            if kind == "rate":
                delta = f"{diff:+.2%}"
            else:
                delta = f"{diff:+.2f}"
        print(f"{label:<22}{fmt(base_value):>14}{fmt(current_value):>14}{delta:>14}")

    # ---- 最终状态分布。
    def _status_counts(tasks_by_id: dict[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in tasks_by_id.values():
            status = (task.get("metrics") or {}).get("final_status") or "无响应"
            counts[status] = counts.get(status, 0) + 1
        return counts

    baseline_statuses = _status_counts(baseline_tasks)
    current_statuses = _status_counts(current_tasks)
    all_statuses = sorted(set(baseline_statuses) | set(current_statuses))
    print(f"{'最终状态分布（L）':<22}", end="")
    parts = []
    for status in all_statuses:
        base_n = baseline_statuses.get(status, 0)
        current_n = current_statuses.get(status, 0)
        if base_n == current_n:
            parts.append(f"{status}={current_n}")
        else:
            parts.append(f"{status}={base_n}→{current_n}")
    print("、".join(parts))

    # ---- 汇总。
    current_pass = sum(1 for task in current_tasks.values() if task.get("passed"))
    print()
    print(
        f"当前通过：{current_pass}/{len(current_tasks)}；"
        f"回归 {len(regressions)} 个，改善 {len(improvements)} 个。"
    )
    if regressions:
        print("结论：存在回归（PASS → FAIL）。")
        return 1
    if current_pass < len(current_tasks):
        print("结论：无回归，但当前仍有失败任务。")
        return 1
    print("结论：无回归，当前全部通过。")
    return 0
