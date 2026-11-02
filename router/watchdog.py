#!/usr/bin/env python3
"""watchdog.py — 多 Agent 系统的猎犬 / 体检器。

只查**确定性**的异常，不做内容判断（方向对不对要人看产出）：

    zombie       status=running 且超过 ZOMBIE_AFTER 秒没更新
    missing      任务 done 但 output 路径不存在
    no_output    done 但根本没写 output
    stalled      pending 超过 STALLED_AFTER 秒还没人领
    bad_thread   thread 形如 `e-xxxx`（旧 bug：用事件 id 冒充 thread）
    log_bloat    events.jsonl 超过阈值
    orphan       任务的 parents 指向不存在的任务

能自愈的直接修（僵尸→failed），修不了的在报告里列出来。

⚠️ 踩过的坑：ROOT 必须算到**仓库根**（agent-system/），不是 router/。
   之前写成 dirname(abspath(__file__)) 导致 `sys.path.insert(0, ROOT + "/state")`
   指向 router/state，`import state` 直接失败。

用法
    python3 router/watchdog.py                 # 体检 + 自愈
    python3 router/watchdog.py --dry-run       # 只看不改
    python3 router/watchdog.py --json
    python3 router/watchdog.py --threshold 600 # 自定义僵尸阈值（秒）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

# ---- 路径：仓库根，不是 router/ ----
ROUTER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ROUTER_DIR)
STATE_DIR = os.path.join(ROOT, "state")
sys.path.insert(0, STATE_DIR)

import state  # noqa: E402

ZOMBIE_AFTER = state.ZOMBIE_AFTER      # 45 分钟
STALLED_AFTER = 6 * 3600               # 6 小时
LOG_BLOAT_BYTES = 2 * 1024 * 1024      # 2 MB

# 旧 bug 留下的痕迹：thread 长成这样就是拿事件 id 当线程用了
BAD_THREAD_RE_PREFIX = ("e-", "t-")


def _age_seconds(iso_ts: str) -> float:
    try:
        return (datetime.now() - datetime.fromisoformat(iso_ts)).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def check(zombie_after: float = ZOMBIE_AFTER) -> dict:
    tasks = state.load_tasks()
    issues = []

    for tid, t in tasks.items():
        status = t.get("status")
        age = _age_seconds(t.get("updated_at") or t.get("created_at") or "")

        if status == "running" and age > zombie_after:
            issues.append({
                "kind": "zombie", "task": tid, "worker": t.get("worker"),
                "detail": "running 已 %d 分钟，判定僵尸" % (age // 60),
                "fixable": True,
            })

        if status == "pending" and age > STALLED_AFTER:
            issues.append({
                "kind": "stalled", "task": tid, "worker": t.get("worker"),
                "detail": "pending 已 %d 小时，没人领" % (age // 3600),
                "fixable": False,
            })

        if status == "done":
            out = t.get("output")
            if not out:
                issues.append({"kind": "no_output", "task": tid, "worker": t.get("worker"),
                               "detail": "状态 done 但没写 output 路径", "fixable": False})
            elif not os.path.isabs(str(out)):
                issues.append({"kind": "no_output", "task": tid, "worker": t.get("worker"),
                               "detail": "output 必须是绝对路径：%s" % out, "fixable": False})
            elif not os.path.exists(str(out)):
                issues.append({"kind": "missing", "task": tid, "worker": t.get("worker"),
                               "detail": "产出不存在：%s" % out, "fixable": False})

        thread = t.get("thread") or ""
        if thread.startswith(BAD_THREAD_RE_PREFIX) and t.get("thread_auto") is not False:
            issues.append({"kind": "bad_thread", "task": tid, "worker": t.get("worker"),
                           "detail": "thread=%s 像事件 id 冒充的线程（应走 source-id 生成）" % thread,
                           "fixable": False})

        for parent in t.get("parents") or []:
            if parent not in tasks:
                issues.append({"kind": "orphan", "task": tid, "worker": t.get("worker"),
                               "detail": "parents 指向不存在的任务 %s" % parent,
                               "fixable": False})

    log_size = os.path.getsize(state.EVENTS_PATH) if os.path.exists(state.EVENTS_PATH) else 0
    if log_size > LOG_BLOAT_BYTES:
        issues.append({"kind": "log_bloat", "task": None, "worker": None,
                       "detail": "events.jsonl 已 %.1f MB，考虑归档" % (log_size / 1048576),
                       "fixable": False})

    return {"root": ROOT, "tasks": len(tasks), "log_bytes": log_size, "issues": issues}


def heal(report: dict) -> list:
    """自愈：僵尸任务标记 failed，写清原因。"""
    fixed = []
    for iss in report["issues"]:
        if iss["kind"] == "zombie" and iss.get("fixable"):
            try:
                state.update_task(iss["task"], status="failed",
                                  error="watchdog: 超时未回写，判定僵尸")
                fixed.append(iss["task"])
            except KeyError:
                pass
    return fixed


def render(report: dict, fixed: list) -> str:
    issues = report["issues"]
    lines = ["体检完成：%d 个任务，%d 个问题" % (report["tasks"], len(issues))]
    if not issues:
        lines.append("· 一切正常")
    for kind in ("zombie", "stalled", "missing", "no_output", "bad_thread", "orphan", "log_bloat"):
        group = [i for i in issues if i["kind"] == kind]
        if not group:
            continue
        lines.append("· [%s] %d 条" % (kind, len(group)))
        for i in group[:5]:
            lines.append("    %s %s" % (i.get("task") or "-", i["detail"]))
        if len(group) > 5:
            lines.append("    …还有 %d 条" % (len(group) - 5))
    if fixed:
        lines.append("已自愈：%d 个僵尸任务标记为 failed（%s）" % (len(fixed), "、".join(fixed)))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="watchdog.py", description="僵尸任务 / 产出缺失 / 日志膨胀体检")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不自愈")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--threshold", type=float, default=ZOMBIE_AFTER, help="僵尸阈值（秒）")
    args = ap.parse_args(argv)

    report = check(args.threshold)
    fixed = [] if args.dry_run else heal(report)
    if args.json:
        report["fixed"] = fixed
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report, fixed))
    # 有需要人看的问题就退 1
    remaining = [i for i in report["issues"] if i["kind"] not in ("zombie",)]
    return 1 if remaining else 0


if __name__ == "__main__":
    sys.exit(main())
