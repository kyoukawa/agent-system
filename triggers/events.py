#!/usr/bin/env python3
"""events.py — 事件层：Fast Path / Slow Path 过滤 + Inline & Spill。

Fast Path
    纯正则 + 关键词的确定性过滤，零模型调用。绝大多数「整理收集」「推上去」
    这种话在这里就能定出候选 Worker，成本为零。

Slow Path
    文本过长、或 Fast Path 没把握时，标记 `needs_model=True` 交回给 Router
    （也就是调用方那个 agent）去判断。本模块**不调用任何模型**，只负责标记。

Inline & Spill
    事件正文短就用 `text` 内联；超过 SPILL_THRESHOLD 字符就落到
    `triggers/spill/<event_id>.txt`，事件里只留 `text_path` + `text_preview`，
    避免状态文件和 prompt 被长文撑爆。

事件规范（规范化后）
    {"id": "e-...", "source": "user|todo|file|chat", "source_id": "...",
     "thread": 显式线程名或 null, "text": "...", "ts": "...", ...}

⚠️ thread 规则（踩过的坑）
    事件**没有 thread 时不要用事件 id 冒充**——那样每条事件都变成独立线程，
    直通永久失效。统一由 `derive_thread()` 用 `source-id` 生成；
    自动生成的 thread 会被打上 `thread_auto: true`，router 不让它参与直通匹配。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime

TRIGGER_DIR = os.path.dirname(os.path.abspath(__file__))
SPILL_DIR = os.path.join(TRIGGER_DIR, "spill")

SPILL_THRESHOLD = 1200      # 超过这么多字符就 spill 到文件
SLOW_PATH_THRESHOLD = 600   # 超过这么多字符就同时标 needs_model

# 显式线程名的形状；不满足就用 source-id 自动生成
EXPLICIT_THREAD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,63}$")

# Fast Path 信号库：Worker 名 -> 一组正则。
# 注意这里只是「粗筛」，最终打分在 router.py，两边词表刻意保持独立，
# 因为 router 的词来自 workers/*.json（可被管家改），events.py 的是硬编码兜底。
FAST_SIGNALS = {
    "reading-collector": [r"整理(一下)?收[集藏]", r"处理(一下)?待办", r"收[集藏]这个",
                          r"公众[号文]", r"文章链接", r"笔记归档"],
    "video-notes": [r"视频", r"播客", r"转[写录]", r"逐字稿", r"字幕",
                    r"bilibili\.com", r"youtu\.?be", r"youtube\.com", r"小宇宙"],
    "github-maintainer": [r"推(送|上去|仓)", r"提交(代码)?", r"建(个)?仓(库)?",
                          r"发(个)?release", r"pull request", r"\bpr\b", r"\bissue\b"],
    "corpus-en": [r"英语(作文|语料)", r"英文作文", r"english\s+corpus", r"版书"],
    "corpus-zh": [r"语文(作文|语料)", r"议论文", r"阅卷", r"六维", r"二稿", r"一稿"],
}

_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in FAST_SIGNALS.items()}

# 明确的「不是给 Worker 的活」信号——元角色，不参与路由
META_PATTERNS = [
    re.compile(p, re.I) for p in (
        r"加(一个)?\s*(agent|worker)", r"新建\s*worker", r"系统体检", r"校验(一下)?worker",
        r"改(一下)?.{0,8}(触发词|行为|提示词)", r"这些\s*agent.{0,6}(问题|怎么样)",
    )
]


# --------------------------------------------------------------------------
# 规范化
# --------------------------------------------------------------------------

def derive_thread(event: dict) -> str:
    """没有显式 thread 时，用 `source-source_id` 生成稳定线程名。

    同一来源的同一条东西（比如同一个待办条目被拆成两条事件）会落进同一线程，
    从而能走直通。**绝不使用事件 id**——事件 id 每次都是新的，直通会永远命不中。
    """
    if event.get("thread"):
        return str(event["thread"])
    source = str(event.get("source") or "user")
    sid = event.get("source_id")
    if sid is None or str(sid).strip() == "":
        sid = "untracked"
    slug = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(sid)).strip("-")[:48] or "untracked"
    return "%s-%s" % (source, slug)


def is_explicit_thread(event: dict) -> bool:
    """这个 thread 是调用方显式声明的吗？（自动生成的不参与直通匹配）"""
    t = event.get("thread")
    return bool(t) and bool(EXPLICIT_THREAD_RE.match(str(t)))


def _event_id(seed: str) -> str:
    return "e-" + hashlib.sha1((seed + str(time.time())).encode("utf-8")).hexdigest()[:12]


def spill(text: str, event_id: str) -> str:
    """长文本落盘，返回相对于仓库根的路径（便于跨机器/跨会话引用）。"""
    os.makedirs(SPILL_DIR, exist_ok=True)
    path = os.path.join(SPILL_DIR, "%s.txt" % event_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return os.path.relpath(path, os.path.dirname(TRIGGER_DIR))


# --------------------------------------------------------------------------
# Fast / Slow Path
# --------------------------------------------------------------------------

def fast_path(text: str) -> dict:
    """零成本的确定性粗筛。返回 hits（worker -> 命中词列表）与 meta 标记。"""
    hits = {}
    for worker, pats in _COMPILED.items():
        found = [p.pattern for p in pats if p.search(text)]
        if found:
            hits[worker] = found
    meta = [p.pattern for p in META_PATTERNS if p.search(text)]
    return {"hits": hits, "meta": meta}


def slow_path_needed(text: str, fast: dict) -> bool:
    """Fast Path 没命中、或者多个 Worker 打平、或者文本太长 —— 都需要模型再看一眼。"""
    if len(text) > SLOW_PATH_THRESHOLD:
        return True
    if fast["meta"]:
        return False          # 元角色请求，不交给 Worker，也不需要模型再分派
    if not fast["hits"]:
        return True
    return len(fast["hits"]) > 1


def normalize(raw: dict) -> dict:
    """把任意来源的原始事件规整成标准事件。幂等。"""
    if not isinstance(raw, dict):
        raise ValueError("事件必须是 dict，收到 %r" % type(raw).__name__)

    text = raw.get("text")
    if text is None:
        text = raw.get("content") or raw.get("body") or ""
    text = str(text)

    ev = {
        "id": raw.get("id") or _event_id(str(raw.get("source")) + text[:40]),
        "source": raw.get("source") or "user",
        "source_id": raw.get("source_id"),
        "ts": raw.get("ts") or datetime.now().isoformat(timespec="seconds"),
        "kind": raw.get("kind") or "text",
    }
    for key in ("title", "url", "lang", "priority"):
        if raw.get(key) is not None:
            ev[key] = raw[key]

    # ---- thread：显式优先，否则 source-id 自动生成 ----
    explicit = is_explicit_thread(raw)
    ev["thread"] = derive_thread(raw)
    ev["thread_auto"] = not explicit

    # ---- Inline & Spill ----
    if len(text) > SPILL_THRESHOLD:
        ev["text_path"] = spill(text, ev["id"])
        ev["text_preview"] = text[:200]
        ev["text"] = text[:200]
    else:
        ev["text"] = text

    # ---- Fast / Slow Path ----
    fast = fast_path(text)
    ev["fast_hits"] = fast["hits"]
    ev["is_meta"] = bool(fast["meta"])
    ev["path"] = "slow" if slow_path_needed(text, fast) else "fast"
    ev["needs_model"] = ev["path"] == "slow"
    return ev


def normalize_many(raws) -> list:
    return [normalize(r) for r in raws]


def load_events(path: str) -> list:
    """读事件：支持 .json（数组或单个对象）与 .jsonl（一行一条）。"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return []
    if path.endswith(".jsonl"):
        return [json.loads(line) for line in content.splitlines() if line.strip()]
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [json.loads(line) for line in content.splitlines() if line.strip()]
    return data if isinstance(data, list) else [data]


def explain(ev: dict) -> str:
    """一行人类可读说明，调试用。"""
    return "[%s] thread=%s(auto=%s) path=%s hits=%s meta=%s" % (
        ev["id"], ev["thread"], ev["thread_auto"], ev["path"],
        ",".join(sorted(ev.get("fast_hits", {}))) or "-", ev.get("is_meta"))


def _cli(argv):
    """events.py 只做自检，不参与主流程。"""
    sample = argv[1] if len(argv) > 1 else "帮我整理收集一下待办"
    ev = normalize({"text": sample, "source": "user", "source_id": "cli"})
    print(explain(ev))
    print(json.dumps({k: v for k, v in ev.items() if k != "text"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv))
