#!/usr/bin/env python3
"""router.py — 多 Agent 系统的路由层。

**只分派，不上手。** 本文件里没有任何抓取/转写/git 逻辑，只有判断。

三种分派结果
    route     唯一最高分 Worker → 直接派
    continue  thread 已有归属 → 直通，续接原 Worker，不再重新判断
    ambiguous 多个 Worker 同分 → 返回候选名单，让用户选，**绝不瞎猜**
    unrouted  没有任何 Worker 命中 → 报给用户，**不硬接**
    meta      命中元角色（加 Worker / 体检 / 改配置）→ 不路由，直接交给人

直通规则（踩过的坑）
    * 只有 `thread_auto == False`（调用方显式声明的 thread）才参与直通匹配。
      自动从 `source-id` 生成的 thread 不参与——否则「同一个来源」会被误当成
      「同一件事的续篇」，把不相干的活塞给上一个 Worker。
    * 直通只在**没有显式 to 字段**、并且 `state.thread_owner(thread)` 有归属时生效。

用法
    python3 router/router.py --text "帮我整理收集一下待办"
    python3 router/router.py --text "..." --thread work-2026-11 --source todo --source-id 123
    python3 router/router.py --events events.jsonl
    python3 router/router.py workers        # 列出所有 Worker 及其触发词
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# 路径：ROOT 必须是仓库根，不是 router/（watchdog 曾在这里翻过车）
# ---------------------------------------------------------------------------
ROUTER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ROUTER_DIR)
WORKERS_DIR = os.path.join(ROOT, "workers")

sys.path.insert(0, os.path.join(ROOT, "state"))
sys.path.insert(0, os.path.join(ROOT, "triggers"))

import state      # noqa: E402
import events     # noqa: E402

# 元角色：有技能，但不参与路由，也不该有 worker 定义
META_SKILLS = ("agent-router", "system-steward", "skill-creator", "guided-learning")

# 同分判歧义的阈值规则：
#   最高分 == 次高分 且 最高分 > 0  → ambiguous
AMBIGUOUS_ON_TIE = True


# ---------------------------------------------------------------------------
# Worker 加载
# ---------------------------------------------------------------------------

def load_workers(include_disabled: bool = False) -> list:
    """读 workers/*.json。写坏的文件不该让整个路由挂掉，只跳过并告警。"""
    out = []
    if not os.path.isdir(WORKERS_DIR):
        return out
    for fn in sorted(os.listdir(WORKERS_DIR)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(WORKERS_DIR, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                w = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            sys.stderr.write("[router] 跳过坏定义 %s: %s\n" % (fn, e))
            continue
        w["_file"] = fn
        w.setdefault("name", os.path.splitext(fn)[0])
        w["_enabled"] = bool(w.get("enabled", True))
        if not w["_enabled"] and not include_disabled:
            continue
        out.append(w)
    return out


# ---------------------------------------------------------------------------
# 打分
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return str(s).lower().strip()


def score(text: str, worker: dict) -> dict:
    """给一个 Worker 打分。

    1 个触发词命中计 1 分；同一个词在一个 Worker 里只计一次。
    命中词越长权重越高（长词更具体），用 len 做加权：score += 1 + len(t)/10。
    """
    t = _norm(text)
    matched, total = [], 0.0
    for trig in worker.get("triggers") or []:
        key = _norm(trig)
        if not key:
            continue
        if key in t:
            matched.append(trig)
            total += 1.0 + len(key) / 10.0
    # 来源加成：事件来源在 worker.sources 里，说明是天然匹配（比如 todo 事件给 reading-collector）
    return {"worker": worker["name"], "score": round(total, 2), "matched": matched}


def rank(text: str, workers: list, source: str = None) -> list:
    scored = [score(text, w) for w in workers]
    if source:
        for w in workers:
            if source in (w.get("sources") or []):
                for s in scored:
                    if s["worker"] == w["name"] and s["score"] > 0:
                        s["score"] = round(s["score"] + 0.5, 2)
                        s["source_bonus"] = True
    scored.sort(key=lambda x: (-x["score"], x["worker"]))
    return scored


# ---------------------------------------------------------------------------
# 路由主逻辑
# ---------------------------------------------------------------------------

def route_one(ev: dict, workers: list) -> dict:
    """对单条已规范化事件给出计划项。返回 plan dict，不含写状态。"""
    text = ev.get("text") or ""

    base = {
        "event_id": ev.get("id"),
        "thread": ev.get("thread"),
        "thread_auto": ev.get("thread_auto", True),
        "path": ev.get("path", "fast"),
    }

    # 0) 元角色请求：不路由
    if ev.get("is_meta"):
        return dict(base, action="meta", worker=None, mode="none", score=0,
                    candidates=[], why="命中元角色信号（加 Worker / 体检 / 改配置），交 system-steward 或直接处理")

    # 1) 直通：只看显式 thread
    if not ev.get("thread_auto") and ev.get("thread"):
        owner = state.thread_owner(ev["thread"])
        if owner:
            return dict(base, action="continue", worker=owner, mode="continue",
                        score=None, candidates=[owner],
                        why="thread %r 已归属 %s，直通" % (ev["thread"], owner))

    # 2) 正常打分
    scored = rank(text, workers, source=ev.get("source"))
    positive = [s for s in scored if s["score"] > 0]
    if not positive:
        return dict(base, action="unrouted", worker=None, mode="none", score=0,
                    candidates=[], why="没有任何 Worker 命中，报给用户，不硬接")

    top = positive[0]
    ties = [s for s in positive if s["score"] == top["score"]]
    if AMBIGUOUS_ON_TIE and len(ties) > 1:
        return dict(base, action="ambiguous", worker=None, mode="none",
                    score=top["score"], candidates=sorted(s["worker"] for s in ties),
                    why="%d 个 Worker 同分 %.2f，让用户选" % (len(ties), top["score"]))

    return dict(base, action="route", worker=top["worker"], mode="new",
                score=top["score"], candidates=[top["worker"]],
                matched=top["matched"],
                why="命中 %s（%s）" % (top["worker"], "、".join(top["matched"])))


def route(events_: list, workers: list, persist: bool = True) -> list:
    """批量路由。persist=True 时把 route/continue 写成任务、写事件流水。"""
    plan = []
    for ev in events_:
        p = route_one(ev, workers)
        if persist:
            state.append_event(ev)
            if p["action"] in ("route", "continue"):
                parents = []
                if p["action"] == "continue":
                    prev = state.load_threads().get(p["thread"], {}).get("last_task")
                    if prev:
                        parents = [prev]
                task = state.create_task(
                    thread=p["thread"], worker=p["worker"], event=ev,
                    mode=p["mode"], parents=parents,
                )
                p["task"] = task["id"]
            elif p["action"] in ("ambiguous", "unrouted", "meta"):
                p["task"] = None
        plan.append(p)
    return plan


def attach_worker_prompts(plan: list, workers: list) -> list:
    """给 plan 里的每个 route/continue 项补上 worker 的 prompt（调用方拿去开子代理）。"""
    by_name = {w["name"]: w for w in workers}
    for p in plan:
        w = by_name.get(p.get("worker"))
        if w:
            p["prompt"] = w.get("prompt", "")
            p["skill"] = w.get("skill")
            p["tools"] = w.get("tools", [])
    return plan


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

ACTION_LABEL = {
    "route": "派给",
    "continue": "续接",
    "ambiguous": "歧义",
    "unrouted": "未归属",
    "meta": "元角色",
}


def render(plan: list) -> str:
    if not plan:
        return "（没有事件）"
    lines = []
    for p in plan:
        head = "%-6s" % ACTION_LABEL.get(p["action"], p["action"])
        who = p.get("worker")
        if not who:
            who = "[" + ",".join(p.get("candidates") or []) + "]" if p.get("candidates") else "-"
        who = "%-20s" % who
        score = "" if p.get("score") is None else " (%.2f)" % p["score"]
        lines.append("%s %s%s  thread=%s%s" % (
            head, who, score, p.get("thread"),
            "  " + ("[auto]" if p.get("thread_auto") else "")))
        lines.append("        为什么: %s" % p.get("why"))
        if p.get("task"):
            lines.append("        任务: %s  mode=%s" % (p["task"], p.get("mode")))
    return "\n".join(lines)


def plan_json(plan: list) -> str:
    return json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"),
                       "plan": plan}, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_workers(workers: list) -> int:
    print("共 %d 个 Worker（仓库根 %s）\n" % (len(workers), ROOT))
    for w in workers:
        print("- %-20s %s" % (w["name"], w.get("purpose", "")))
        print("    skill:    %s" % w.get("skill"))
        print("    triggers: %s" % "、".join(w.get("triggers") or []))
        print("    sources:  %s" % "、".join(w.get("sources") or []))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="router.py",
        description="多 Agent 路由：只分派，不上手。")
    ap.add_argument("--text", help="直接把一句话当成 user 事件")
    ap.add_argument("--events", help="事件文件（.json 数组 / .jsonl）")
    ap.add_argument("--thread", help="显式声明的 thread（启用直通）")
    ap.add_argument("--source", default="user", help="事件来源，默认 user")
    ap.add_argument("--source-id", dest="source_id", help="来源侧 id，用于自动生成 thread")
    ap.add_argument("--dry-run", action="store_true", help="只出计划，不写状态")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--with-prompt", action="store_true", help="把 worker 的 prompt 一并带出")
    ap.add_argument("cmd", nargs="?", help="子命令：workers")
    args = ap.parse_args(argv)

    workers = load_workers()

    if args.cmd == "workers":
        return cmd_workers(workers)

    if not workers:
        sys.stderr.write("[router] 警告：workers/ 下没有任何启用的 Worker\n")

    if args.text is not None:
        raw = [{"text": args.text, "source": args.source, "source_id": args.source_id,
                "thread": args.thread}]
    elif args.events:
        raw = events.load_events(args.events)
    else:
        ap.print_help()
        return 2

    evs = events.normalize_many(raw)
    persist = not args.dry_run
    plan = route(evs, workers, persist=persist)
    if args.with_prompt:
        attach_worker_prompts(plan, workers)

    if args.json:
        print(plan_json(plan))
    else:
        print(render(plan))
        if not persist:
            print("\n（dry-run：状态未写入）")
        else:
            codes = {p["action"] for p in plan}
            if "ambiguous" in codes or "unrouted" in codes:
                print("\n⚠️ 有事件需要用户裁决，不要自己挑。")
    # 有需要人介入的情况，退出码 3，方便脚本感知
    if any(p["action"] in ("ambiguous", "unrouted") for p in plan):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
