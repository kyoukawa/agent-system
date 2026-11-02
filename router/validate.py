#!/usr/bin/env python3
"""validate.py — 校验 workers/*.json 的健康度。

分级
    error  必须修（会让路由出错 / 技能对不上）
    warn   建议修（会让人选错）
    info   提示（不影响运行）

检查项
    E1 必填字段缺失：name / purpose / triggers / sources / prompt
    E2 skill 路径不存在
    E3 SKILL.md front-matter 的 name 与 worker name 不一致（这个坑真踩过：
       github-maintainer 的 front-matter 曾写成 reading-collector）
    E4 WORKER 名与文件名不一致
    E5 元角色（agent-router / system-steward / skill-creator / guided-learning）
       居然有 worker 定义 —— 元角色不参与路由
    E6 triggers 为空 / 全是泛词
    W1 触发词跨 Worker 冲突（同一个词被两个 Worker 认领）
    W2 sources 跨 Worker 冲突
    W3 triggers 里有「整理 / 处理 / 干活」这类泛词
    W4 触发词过短（≤1 字符）或全是英文单词却没有中文场景
    I1 prompt 里没写「返回」——Router 汇总时拿不到结论
    I2 prompt 太长（> 800 字）/ 太短（< 40 字）
    I3 技能文件不存在对应 worker（孤立技能，且不是元角色）

用法
    python3 router/validate.py              # 全量
    python3 router/validate.py corpus-en    # 单个
    python3 router/validate.py --json
    python3 router/validate.py --strict     # warn 也当 error
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

ROUTER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ROUTER_DIR)
WORKERS_DIR = os.path.join(ROOT, "workers")
SKILLS_DIR = "/var/minis/skills"

META_SKILLS = ("agent-router", "system-steward", "skill-creator", "guided-learning")
REQUIRED = ("name", "purpose", "triggers", "sources", "prompt")
GENERIC_TRIGGERS = ("整理", "处理", "干活", "帮忙", "弄一下", "看看", "搞一下")

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.M)


def read_front_matter(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(4000)
    except OSError:
        return {}
    m = FM_RE.match(head)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


class Report:
    def __init__(self):
        self.rows = []   # (level, worker, code, msg)

    def add(self, level, worker, code, msg):
        self.rows.append((level, worker, code, msg))

    def count(self, level):
        return sum(1 for r in self.rows if r[0] == level)

    def dump(self):
        icons = {"error": "✗", "warn": "!", "info": "·"}
        for level in ("error", "warn", "info"):
            rows = [r for r in self.rows if r[0] == level]
            if not rows:
                continue
            print("\n%s (%d)" % (level.upper(), len(rows)))
            for _, worker, code, msg in rows:
                print("  %s %-22s %-4s %s" % (icons[level], worker or "-", code, msg))


def load_all() -> list:
    out = []
    for path in sorted(glob.glob(os.path.join(WORKERS_DIR, "*.json"))):
        fn = os.path.basename(path)
        rec = {"_file": fn, "_path": path, "_stem": os.path.splitext(fn)[0]}
        try:
            with open(path, "r", encoding="utf-8") as f:
                rec.update(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            rec["_parse_error"] = str(e)
        out.append(rec)
    return out


def validate_one(w: dict, rep: Report) -> None:
    name = w.get("name") or w["_stem"]

    if w.get("_parse_error"):
        rep.add("error", name, "E0", "JSON 解析失败：%s" % w["_parse_error"])
        return

    # E1 必填字段
    for field in REQUIRED:
        val = w.get(field)
        if val in (None, "", [], {}):
            rep.add("error", name, "E1", "缺必填字段 %s" % field)

    # E4 name 与文件名
    if w.get("name") != w["_stem"]:
        rep.add("error", name, "E4", "name=%r 与文件名 %s 不一致" % (w.get("name"), w["_stem"]))

    # E5 元角色不该有 worker 定义
    if name in META_SKILLS:
        rep.add("error", name, "E5", "元角色不参与路由，不该有 worker 定义（删掉这个文件）")

    # E2 / E3 技能文件
    skill = w.get("skill")
    if skill:
        if not os.path.exists(skill):
            rep.add("error", name, "E2", "技能文件不存在：%s" % skill)
        else:
            fm = read_front_matter(skill)
            fm_name = (fm.get("name") or "").strip()
            if not fm_name:
                rep.add("warn", name, "E3", "SKILL.md 缺 front-matter name")
            elif fm_name != name:
                rep.add("error", name, "E3",
                        "SKILL.md front-matter name=%r，与 worker name=%r 不一致" % (fm_name, name))
    elif "skill" not in w:
        rep.add("warn", name, "E2", "未声明 skill 字段")

    # E6 / W3 / W4 触发词
    triggers = w.get("triggers") or []
    if not isinstance(triggers, list):
        rep.add("error", name, "E6", "triggers 必须是数组")
        triggers = []
    if not triggers:
        rep.add("error", name, "E6", "triggers 为空，永远不会被路由到")
    for t in triggers:
        st = str(t)
        if st in GENERIC_TRIGGERS:
            rep.add("warn", name, "W3", "触发词「%s」太泛，容易被别的 Worker 抢" % st)
        if len(st.strip()) <= 1:
            rep.add("warn", name, "W4", "触发词「%s」太短，会误命中" % st)
    if len(set(triggers)) != len(triggers):
        rep.add("warn", name, "W5", "triggers 内有重复项")

    # I1 / I2 prompt 质量
    prompt = w.get("prompt") or ""
    if prompt:
        if not any(k in prompt for k in ("返回", "汇报", "产出")):
            rep.add("info", name, "I1", "prompt 没写「返回什么」，Router 汇总时会缺信息")
        if len(prompt) < 40:
            rep.add("info", name, "I2", "prompt 太短（%d 字），子代理可能无法独立执行" % len(prompt))
        if len(prompt) > 800:
            rep.add("info", name, "I2", "prompt 过长（%d 字），细节应写进 SKILL.md" % len(prompt))
        if "SKILL" not in prompt.upper():
            rep.add("info", name, "I3", "prompt 没提 SKILL 文件，子代理可能不读手册")

    # tools / sources 类型
    for field in ("sources", "tools"):
        v = w.get(field)
        if v is not None and not isinstance(v, list):
            rep.add("error", name, "E7", "%s 必须是数组" % field)


def validate_cross(workers: list, rep: Report) -> None:
    """跨 Worker 冲突：触发词 / 来源。"""
    trig_owner: dict = {}
    src_owner: dict = {}
    for w in workers:
        name = w.get("name") or w["_stem"]
        for t in (w.get("triggers") or []):
            key = str(t).strip().lower()
            if not key:
                continue
            trig_owner.setdefault(key, []).append(name)
        for s in (w.get("sources") or []):
            key = str(s).strip().lower()
            if key:
                src_owner.setdefault(key, []).append(name)

    for key, owners in sorted(trig_owner.items()):
        if len(owners) > 1:
            rep.add("warn", ",".join(owners), "W1",
                    "触发词「%s」被 %d 个 Worker 认领 → 会报 ambiguous" % (key, len(owners)))

    # 来源重叠本身不是问题（`user` 谁的活都能说），真正要防的是「同来源、触发词又分不开」。
    benign_sources = ("user", "file", "image")
    for key, owners in sorted(src_owner.items()):
        if len(owners) < 2 or key in benign_sources:
            continue
        rep.add("warn", ",".join(owners), "W2",
                "来源「%s」被 %d 个 Worker 声明，确认触发词能区分" % (key, len(owners)))

    # 显式列出的同义词冲突：语义重叠但不完全同字，人工维护
    for a, b, note in (("corpus-en", "corpus-zh", "两个语料库判据不同，触发词必须有区分度"),
                       ("video-notes", "reading-collector", "视频链接既可能归转写也可能归收集")):
        if {a, b} <= {w.get("name") for w in workers}:
            rep.add("info", "%s|%s" % (a, b), "I4", note)


def find_orphan_skills(workers: list, rep: Report) -> None:
    """技能存在但没有 worker 定义（元角色除外）。"""
    if not os.path.isdir(SKILLS_DIR):
        return
    have = {w.get("name") for w in workers}
    for d in sorted(os.listdir(SKILLS_DIR)):
        p = os.path.join(SKILLS_DIR, d, "SKILL.md")
        if not os.path.isfile(p):
            continue
        if d in have or d in META_SKILLS:
            continue
        rep.add("info", d, "I5", "有技能但无 worker 定义（要么加 worker，要么是元角色需登记）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="validate.py", description="校验 Worker 定义健康度")
    ap.add_argument("names", nargs="*", help="只校验指定 Worker（默认全量）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="warn 也视为失败")
    args = ap.parse_args(argv)

    all_workers = load_all()
    rep = Report()

    targets = all_workers
    if args.names:
        wanted = set(args.names)
        targets = [w for w in all_workers if (w.get("name") or w["_stem"]) in wanted]
        missing = wanted - {(w.get("name") or w["_stem"]) for w in targets}
        for m in sorted(missing):
            rep.add("error", m, "E8", "找不到 workers/%s.json" % m)

    if not all_workers:
        rep.add("error", "-", "E9", "workers/ 下没有任何 .json 定义")

    for w in targets:
        validate_one(w, rep)
    if not args.names:
        validate_cross(all_workers, rep)
        find_orphan_skills(all_workers, rep)

    n_ext = sum(1 for w in all_workers if not w.get("_parse_error"))
    if args.json:
        print(json.dumps({
            "root": ROOT,
            "workers": n_ext,
            "error": rep.count("error"),
            "warn": rep.count("warn"),
            "info": rep.count("info"),
            "rows": [{"level": l, "worker": w, "code": c, "msg": m} for l, w, c, m in rep.rows],
        }, ensure_ascii=False, indent=2))
    else:
        print("校验 %d 个 Worker（%s）" % (n_ext, WORKERS_DIR))
        rep.dump()
        print("\nerror %d / warn %d / info %d" % (
            rep.count("error"), rep.count("warn"), rep.count("info")))

    if rep.count("error"):
        return 1
    if args.strict and rep.count("warn"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
