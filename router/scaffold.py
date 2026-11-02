#!/usr/bin/env python3
"""scaffold.py — 一键生成新 Worker 的骨架。

生成两样东西（名字必须一致，否则 validate.py 会报 E3）：
    1. workers/<name>.json    能力声明（触发词、来源、prompt）
    2. <skills-dir>/<name>/SKILL.md   操作手册骨架，满屏 TODO 等人写

默认 --skills-dir 是 /var/minis/skills。--dry-run 只打印不落盘。

用法
    python3 router/scaffold.py finance-tracker \
        --purpose "记录日常收支并做月度复盘" \
        --triggers "记账,收支,月度复盘" \
        --sources "user" \
        --tools shell_execute,file_read \
        --dry-run

    # 确认后去掉 --dry-run 再跑一次
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

ROUTER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ROUTER_DIR)
WORKERS_DIR = os.path.join(ROOT, "workers")

DEFAULT_SKILLS_DIR = "/var/minis/skills"
META_SKILLS = ("agent-router", "system-steward", "skill-creator", "guided-learning")
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

WORKER_TMPL = {
    "name": "{name}",
    "purpose": "{purpose}",
    "skill": "{skill_path}",
    "enabled": True,
    "triggers": "{triggers}",
    "sources": "{sources}",
    "tools": "{tools}",
    "model": "default",
    "prompt": (
        "你是 {name}。按 {skill_path} 执行，动手前先读 /var/minis/memory/GLOBAL.md 与这份 SKILL。\n"
        "职责：{purpose}\n"
        "只做职责范围内的事，越界交给 Router。\n"
        "返回：状态(成功/失败)、产出绝对路径、一句话结论。不复述过程。"
    ),
}

SKILL_TMPL = """---
name: {name}
description: {purpose}。用户说「{first_trigger}」时使用。TODO:补一句触发场景。
---

> **开工前先读 `/var/minis/memory/GLOBAL.md`**（环境事实、协作铁律、已知坑），读完再动手。

# {name}

{purpose}

## 输入

- 从哪拿：
- 什么格式：
- 没有输入时怎么办：

## 流程

1. TODO
2. TODO
3. TODO

> 每步写清实际命令，不要写「适当处理」这种话。

## 产出

- 落盘路径：
- 结构：
- 文件命名：

## 铁律

- TODO：这个 Worker 绝不能违反的一条（例如「不落盘就不勾完成」）。

## 汇报

给 Router 返回三行：
```
状态：成功 / 失败
产出：/绝对/路径
结论：一句话
```

<!-- scaffold 生成于 {ts}，写实后删掉所有 TODO -->
"""


def build(name: str, args) -> tuple:
    triggers = [t.strip() for t in args.triggers.split(",") if t.strip()]
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    tools = [t.strip() for t in args.tools.split(",") if t.strip()]

    skill_dir = os.path.join(args.skills_dir, name)
    skill_path = os.path.join(skill_dir, "SKILL.md")

    worker = dict(WORKER_TMPL)
    worker["triggers"] = triggers
    worker["sources"] = sources
    worker["tools"] = tools
    worker["purpose"] = args.purpose
    worker["name"] = name
    worker["skill"] = skill_path
    worker["prompt"] = WORKER_TMPL["prompt"].format(
        name=name, purpose=args.purpose, skill_path=skill_path)

    skill = SKILL_TMPL.format(
        name=name, purpose=args.purpose,
        first_trigger=triggers[0] if triggers else name,
        ts=datetime.now().strftime("%Y-%m-%d %H:%M"))

    return worker, skill, skill_dir, skill_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scaffold.py", description="生成新 Worker 骨架")
    ap.add_argument("name", help="Worker 名，小写连字符，如 finance-tracker")
    ap.add_argument("--purpose", required=True, help="一句话职责")
    ap.add_argument("--triggers", required=True, help="触发词，逗号分隔")
    ap.add_argument("--sources", default="user", help="来源类型，逗号分隔（默认 user）")
    ap.add_argument("--tools", default="shell_execute,file_read,file_write",
                    help="可用工具，逗号分隔")
    ap.add_argument("--skills-dir", default=DEFAULT_SKILLS_DIR)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    args = ap.parse_args(argv)

    name = args.name.strip()
    if not NAME_RE.match(name):
        print("✗ 名字不合规：%r（要小写连字符，如 finance-tracker）" % name)
        return 2
    if name in META_SKILLS:
        print("✗ %s 是元角色，不参与路由，不该有 worker 定义" % name)
        return 2

    worker_path = os.path.join(WORKERS_DIR, "%s.json" % name)
    worker, skill, skill_dir, skill_path = build(name, args)

    # 查重提示：不阻塞，只提醒
    warnings = []
    for fn in sorted(os.listdir(WORKERS_DIR)) if os.path.isdir(WORKERS_DIR) else []:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(WORKERS_DIR, fn), "r", encoding="utf-8") as f:
                other = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        overlap = set(t.lower() for t in other.get("triggers") or []) & set(t.lower() for t in worker["triggers"])
        if overlap:
            warnings.append("%s 已占用触发词：%s" % (other.get("name") or fn, "、".join(sorted(overlap))))
    if os.path.exists(worker_path) and not args.force:
        warnings.append("workers/%s.json 已存在（加 --force 覆盖），或者直接用 validate.py 检查它" % name)

    payload = json.dumps(worker, ensure_ascii=False, indent=2) + "\n"

    if args.dry_run:
        print("== 将写入 %s ==" % worker_path)
        print(payload)
        print("== 将写入 %s ==" % skill_path)
        print(skill)
        if warnings:
            print("⚠️  " + "\n⚠️  ".join(warnings))
        print("（dry-run：没有落盘）")
        return 0

    os.makedirs(WORKERS_DIR, exist_ok=True)
    os.makedirs(skill_dir, exist_ok=True)
    with open(worker_path, "w", encoding="utf-8") as f:
        f.write(payload)
    if os.path.exists(skill_path) and not args.force:
        print("· 技能已存在，保留原文件：%s" % skill_path)
    else:
        with open(skill_path, "w", encoding="utf-8") as f:
            f.write(skill)

    print("✓ 生成 workers/%s.json" % name)
    print("✓ 技能 %s" % skill_path)
    for w in warnings:
        print("⚠️  " + w)
    print("\n下一步：")
    print("  1. 把 %s 里的 TODO 写实" % skill_path)
    print("  2. python3 router/validate.py %s" % name)
    print("  3. python3 router/router.py --text \"会触发它的那句话\" --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
