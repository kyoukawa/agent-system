#!/usr/bin/env python3
"""state.py — 多 Agent 系统的状态层（存算分离 + 链式召回）。

设计要点
--------
* **存算分离**：本模块只管「存」，不做任何路由/判断；路由判断在 router/router.py。
* **原子写**：先写临时文件再 os.replace，写到一半被杀也不会留下半截 JSON。
* **损坏自愈**：JSON 解析失败时把坏文件改名成 `*.corrupt.<ts>` 并返回空表，不阻塞调用方。
* **链式召回**：recall(task_id) 沿 `parents` 图逐跳展开，返回整条因果链。

数据文件（均在 state/ 下，与代码同目录）
    tasks.json   任务表：id -> task dict
    threads.json 线程表：thread -> {worker, first_task, last_task, count}
    events.jsonl 事件流水（追加写，供审计/猎犬追查）

task dict 字段
    id        任务 id，`t-<hex>`
    thread    归属线程（显式声明 or `source-<n>` 自动生成）
    worker    负责的 Worker 名
    event     触发的原始事件（截断保存）
    status    pending | running | done | failed
    mode      new | continue
    output    产出路径
    error     失败原因
    created_at / updated_at   ISO8601 本地时间
    parents   父任务 id 列表（链式召回的边）
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime

STATE_DIR = os.path.dirname(os.path.abspath(__file__))
TASKS_PATH = os.path.join(STATE_DIR, "tasks.json")
THREADS_PATH = os.path.join(STATE_DIR, "threads.json")
EVENTS_PATH = os.path.join(STATE_DIR, "events.jsonl")

STATUSES = ("pending", "running", "done", "failed")
# 超过这个秒数还挂在 running，就认为是僵尸（watchdog 会抓）
ZOMBIE_AFTER = 45 * 60


# --------------------------------------------------------------------------
# 基础 IO
# --------------------------------------------------------------------------

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write(path: str, data) -> None:
    """写临时文件 → fsync → os.replace。中途被杀也不会留半截文件。"""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_json(path: str, default):
    """读 JSON；文件不存在或损坏都返回 default，损坏时先备份。"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        backup = "%s.corrupt.%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
        try:
            os.replace(path, backup)
            sys.stderr.write("[state] 损坏文件已备份为 %s (%s)\n" % (backup, e))
        except OSError:
            pass
        return default


def load_tasks() -> dict:
    data = _load_json(TASKS_PATH, {})
    return data if isinstance(data, dict) else {}


def save_tasks(tasks: dict) -> None:
    _atomic_write(TASKS_PATH, tasks)


def load_threads() -> dict:
    data = _load_json(THREADS_PATH, {})
    return data if isinstance(data, dict) else {}


def save_threads(threads: dict) -> None:
    _atomic_write(THREADS_PATH, threads)


# --------------------------------------------------------------------------
# 事件流水
# --------------------------------------------------------------------------

def append_event(event: dict) -> None:
    row = dict(event)
    row.setdefault("logged_at", now())
    with open(EVENTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_events(limit: int = 50) -> list:
    if not os.path.exists(EVENTS_PATH):
        return []
    out = []
    with open(EVENTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out[-limit:]


# --------------------------------------------------------------------------
# 任务 CRUD
# --------------------------------------------------------------------------

def new_task_id() -> str:
    return "t-" + uuid.uuid4().hex[:12]


def create_task(thread: str, worker: str, event=None, mode: str = "new",
                parents=None, task_id: str = None) -> dict:
    tasks = load_tasks()
    tid = task_id or new_task_id()
    t = {
        "id": tid,
        "thread": thread,
        "worker": worker,
        "event": event or {},
        "status": "pending",
        "mode": mode,
        "output": None,
        "error": None,
        "created_at": now(),
        "updated_at": now(),
        "parents": list(parents or []),
    }
    tasks[tid] = t
    save_tasks(tasks)
    _touch_thread(thread, worker, tid)
    return t


def update_task(task_id: str, **fields) -> dict:
    """更新任务字段。status 只能是四种之一，非法值抛 ValueError。"""
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError("status 必须是 %s 之一，收到 %r" % (STATUSES, fields["status"]))
    tasks = load_tasks()
    t = tasks.get(task_id)
    if t is None:
        raise KeyError("任务不存在: %s" % task_id)
    t.update(fields)
    t["updated_at"] = now()
    tasks[task_id] = t
    save_tasks(tasks)
    if "worker" in fields or "thread" in fields:
        _touch_thread(t["thread"], t["worker"], task_id)
    return t


def get_task(task_id: str):
    return load_tasks().get(task_id)


def _touch_thread(thread: str, worker: str, task_id: str) -> None:
    if not thread:
        return
    threads = load_threads()
    rec = threads.get(thread)
    if rec is None:
        threads[thread] = {
            "worker": worker,
            "first_task": task_id,
            "last_task": task_id,
            "count": 1,
            "updated_at": now(),
        }
    else:
        # 显式归属优先于自动推断，这里保持「最后一个真正干活的 worker」不变
        rec["last_task"] = task_id
        rec["count"] = int(rec.get("count", 0)) + 1
        rec["updated_at"] = now()
        if worker and rec.get("worker") in (None, "", "?"):
            rec["worker"] = worker
    save_threads(threads)


def thread_owner(thread: str):
    """线程已有归属就返回 worker 名，否则 None。直通匹配只看显式声明的 thread。"""
    if not thread:
        return None
    rec = load_threads().get(thread)
    return rec.get("worker") if rec else None


# --------------------------------------------------------------------------
# 链式召回
# --------------------------------------------------------------------------

def recall(task_id: str, max_hops: int = 20) -> list:
    """沿 parents 图逐跳展开，返回整条链（自己在前，祖先后）。去环。"""
    tasks = load_tasks()
    chain, seen, frontier = [], set(), [task_id]
    while frontier and len(chain) < max_hops:
        nxt = []
        for tid in frontier:
            if tid in seen or tid not in tasks:
                continue
            seen.add(tid)
            chain.append(tasks[tid])
            nxt.extend(tasks[tid].get("parents") or [])
        frontier = nxt
    return chain


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _fmt_row(t: dict) -> str:
    return "%-18s %-20s %-10s %-9s %s" % (
        t.get("id", "?"), (t.get("thread") or "-")[:20],
        t.get("worker", "?"), t.get("status", "?"),
        (t.get("event") or {}).get("text", "")[:40],
    )


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"

    if cmd == "status":
        tasks = load_tasks()
        counts = {s: 0 for s in STATUSES}
        for t in tasks.values():
            counts[t.get("status", "?")] = counts.get(t.get("status", "?"), 0) + 1
        threads = load_threads()
        print("任务总数: %d" % len(tasks))
        for s in STATUSES:
            print("  %-8s %d" % (s, counts.get(s, 0)))
        print("线程数:   %d" % len(threads))
        pend = [t for t in tasks.values() if t.get("status") in ("pending", "running")]
        if pend:
            print("\n未完成任务:")
            print("%-18s %-20s %-10s %-9s %s" % ("ID", "THREAD", "WORKER", "STATUS", "EVENT"))
            for t in sorted(pend, key=lambda x: x.get("created_at", "")):
                print(_fmt_row(t))
        return 0

    if cmd == "list":
        tasks = load_tasks()
        if not tasks:
            print("（空）")
            return 0
        print("%-18s %-20s %-10s %-9s %s" % ("ID", "THREAD", "WORKER", "STATUS", "EVENT"))
        for t in sorted(tasks.values(), key=lambda x: x.get("created_at", "")):
            print(_fmt_row(t))
        return 0

    if cmd == "threads":
        threads = load_threads()
        if not threads:
            print("（空）")
            return 0
        for name, rec in sorted(threads.items()):
            print("%-24s worker=%-20s count=%s last=%s" % (
                name, rec.get("worker"), rec.get("count"), rec.get("last_task")))
        return 0

    if cmd == "show" and len(argv) > 2:
        t = get_task(argv[2])
        if t is None:
            print("任务不存在: %s" % argv[2])
            return 1
        print(json.dumps(t, ensure_ascii=False, indent=2))
        return 0

    if cmd == "recall" and len(argv) > 2:
        chain = recall(argv[2])
        if not chain:
            print("任务不存在: %s" % argv[2])
            return 1
        depth = 0
        for t in chain:
            print("%s%-18s %-10s %-8s %s" % (
                "  " * depth, t["id"], t.get("worker"), t.get("status"),
                (t.get("event") or {}).get("text", "")[:50]))
            depth += 1
        return 0

    if cmd == "events":
        for e in read_events(int(argv[2]) if len(argv) > 2 else 20):
            print(json.dumps(e, ensure_ascii=False))
        return 0

    print(__doc__)
    print("用法: state.py [status|list|threads|show <id>|recall <id>|events [n]]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
