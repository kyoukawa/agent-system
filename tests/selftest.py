#!/usr/bin/env python3
"""selftest.py — 路由系统的冒烟测试（纯标准库，可进 CI）。

覆盖的用例都是「踩过的坑 + 必须成立的行为」：

    route        正常命中
    ambiguous    多 Worker 同分 → 必须报歧义，不瞎猜
    unrouted     没有任何命中 → 必须报未归属，不硬接
    meta         元角色请求不路由
    continue     显式 thread 第二次事件直通
    no_passthru  自动生成的 thread 不参与直通
    thread       无 thread 时用 source-id 生成，不是事件 id

用法
    python3 tests/selftest.py            # 全部跑一遍
    python3 tests/selftest.py -v

注意：测试用 `--dry-run` 的纯函数接口（router.route(..., persist=False)），
不写任何状态文件，可以反复跑。
"""

from __future__ import annotations

import os
import sys
import traceback

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "router"))
sys.path.insert(0, os.path.join(ROOT, "state"))
sys.path.insert(0, os.path.join(ROOT, "triggers"))

import router     # noqa: E402
import events     # noqa: E402
import state      # noqa: E402

VERBOSE = "-v" in sys.argv


def run_case(text, source="user", source_id=None, thread=None):
    ev = events.normalize({"text": text, "source": source,
                           "source_id": source_id, "thread": thread})
    plan = router.route([ev], router.load_workers(), persist=False)
    return ev, plan[0]


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def test_reading_route():
    ev, p = run_case("帮我整理收集一下待办")
    assert p["action"] == "route", p
    assert p["worker"] == "reading-collector", p


@case
def test_video_route():
    ev, p = run_case("这个视频链接帮我转写一下做逐字稿")
    assert p["action"] == "route", p
    assert p["worker"] == "video-notes", p


@case
def test_github_route():
    ev, p = run_case("推上去，commit hash 给我")
    assert p["action"] == "route", p
    assert p["worker"] == "github-maintainer", p


@case
def test_corpus_zh_route():
    ev, p = run_case("语文作文二稿入库")
    assert p["action"] == "route", p
    assert p["worker"] == "corpus-zh", p


@case
def test_corpus_en_route():
    ev, p = run_case("这批英语语料整理一下")
    assert p["action"] == "route", p
    assert p["worker"] == "corpus-en", p


@case
def test_ambiguous():
    ev, p = run_case("英语语料和语文语料一起整理")
    assert p["action"] == "ambiguous", p
    assert set(p["candidates"]) == {"corpus-en", "corpus-zh"}, p


@case
def test_unrouted():
    ev, p = run_case("今天天气怎么样")
    assert p["action"] == "unrouted", p
    assert p["worker"] is None, p


@case
def test_meta_not_routed():
    ev, p = run_case("帮我加一个 agent 管财务")
    assert p["action"] == "meta", p
    assert p["worker"] is None, p


@case
def test_auto_thread_from_source_id():
    ev, p = run_case("推上去", source="todo", source_id="42")
    assert ev["thread"] == "todo-42", ev["thread"]
    assert ev["thread_auto"] is True
    assert ev["id"] not in ev["thread"], "thread 不能用事件 id 冒充"


@case
def test_auto_thread_no_passthru():
    """自动生成的 thread 不参与直通：即使 threads 表里有归属，也不应 continue。"""
    ev, p = run_case("推上去", source="todo", source_id="42")
    assert p["action"] == "route", p
    assert p["worker"] == "github-maintainer", p


@case
def test_generated_thread_with_entry_still_no_passthru():
    """人为让 todo-42 归属 reading-collector，自动 thread 仍不许直通。"""
    threads = state.load_threads()
    had = "todo-42" in threads
    if not had:
        threads["todo-42"] = {"worker": "reading-collector", "first_task": "x",
                              "last_task": "x", "count": 1}
        state.save_threads(threads)
    try:
        ev, p = run_case("推上去", source="todo", source_id="42")
        assert p["action"] == "route", "自动 thread 不该直通，得到 %r" % p
        assert p["worker"] == "github-maintainer", p
    finally:
        if not had:
            threads.pop("todo-42", None)
            state.save_threads(threads)


@case
def test_explicit_thread_passthru():
    owned = "selftest-thread"
    threads = state.load_threads()
    had = owned in threads
    threads[owned] = {"worker": "reading-collector", "first_task": "x", "last_task": "x", "count": 1}
    state.save_threads(threads)
    try:
        ev, p = run_case("推上去", thread=owned)
        assert ev["thread_auto"] is False
        assert p["action"] == "continue", p
        assert p["worker"] == "reading-collector", p
    finally:
        if not had:
            threads.pop(owned, None)
            state.save_threads(threads)


@case
def test_spill():
    long_text = "整理收集待办。" + ("正文" * 2000)
    ev = events.normalize({"text": long_text, "source": "todo", "source_id": "spill"})
    assert "text_path" in ev, "长文本应 spill 到文件"
    assert len(ev["text"]) <= 200
    path = os.path.join(ROOT, ev["text_path"])
    assert os.path.exists(path)
    assert ev["path"] == "slow"


@case
def test_worker_names_and_skills():
    for w in router.load_workers():
        assert w["name"] not in router.META_SKILLS, "元角色不该有 worker 定义: %s" % w["name"]
        assert os.path.exists(w["skill"]), "技能缺失: %s" % w["skill"]
        assert w["triggers"], "triggers 为空: %s" % w["name"]


@case
def test_status_validation():
    try:
        state.update_task("__not_exist__", status="weird")
        raise AssertionError("应该先因 status 非法而抛 ValueError")
    except ValueError:
        pass


def main():
    passed = failed = 0
    for fn in CASES:
        name = fn.__name__
        try:
            fn()
            passed += 1
            print("  ✓ %s" % name)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("  ✗ %s  →  %s" % (name, e))
            if VERBOSE:
                traceback.print_exc()
    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
