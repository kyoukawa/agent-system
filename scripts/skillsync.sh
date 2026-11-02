#!/bin/sh
# skillsync.sh — 保证技能文件的多份副本一致
#
# 技能散在三处：
#   1. 运行时    /var/minis/skills/<name>/SKILL.md   ← agent 实际读的
#   2. 框架仓    agent-system/skills/<name>/SKILL.md
#   3. 业务仓    reading-collector/skills/<name>/SKILL.md
#
# 用法：
#   sh skillsync.sh           同步（以运行时为准，推到两个仓库）
#   sh skillsync.sh --check   只核对，不一致就报错退出
#   sh skillsync.sh --pull    反向：以仓库为准，覆盖运行时
#
# 真相源约定：**运行时是真相**。改技能先改 /var/minis/skills/，再跑本脚本。

set -eu

AS="${AS_ROOT:-/var/minis/workspace/agent-system}"
RC="${RC_ROOT:-/var/minis/workspace/reading-collector}"
RUNTIME="${SKILLS_DIR:-/var/minis/skills}"

MODE="${1:-sync}"
fail=0

# 框架仓收录全部技能；业务仓只收录它自己的那几个
FRAMEWORK_SKILLS="agent-router system-steward reading-collector video-notes github-maintainer corpus-en corpus-zh"
RC_SKILLS="reading-collector video-notes"   # 业务仓只收与收集相关的技能

copy_one() {
  name="$1"; target="$2"
  src="$RUNTIME/$name/SKILL.md"
  [ -f "$src" ] || { echo "  ! 运行时缺 $name，跳过"; return; }
  mkdir -p "$target/$name"
  cp "$src" "$target/$name/SKILL.md"
  echo "  → $target/$name/SKILL.md"
}

check_one() {
  name="$1"; target="$2"
  src="$RUNTIME/$name/SKILL.md"
  dst="$target/$name/SKILL.md"
  [ -f "$src" ] || { echo "  ! 运行时缺 $name"; return; }
  if [ ! -f "$dst" ]; then
    echo "  ✗ $name 在 $(basename "$target") 缺失"; fail=1; return
  fi
  if cmp -s "$src" "$dst"; then
    echo "  ✓ $name"
  else
    echo "  ✗ $name 不一致：$dst"
    fail=1
  fi
}

pull_one() {
  name="$1"
  dst="$RUNTIME/$name/SKILL.md"
  for t in "$AS/skills" "$RC/skills"; do
    if [ -f "$t/$name/SKILL.md" ]; then
      mkdir -p "$RUNTIME/$name"
      cp "$t/$name/SKILL.md" "$dst"
      echo "  ← 从 $(basename "$(dirname "$t")")/$name 拉回运行时"
      return
    fi
  done
}

case "$MODE" in
  --check)
    echo "== 核对技能副本 =="
    for s in $FRAMEWORK_SKILLS; do check_one "$s" "$AS/skills"; done
    for s in $RC_SKILLS; do check_one "$s" "$RC/skills"; done
    if [ "$fail" -eq 1 ]; then
      echo
      echo "存在不一致。跑 'sh skillsync.sh' 以运行时为准同步，"
      echo "或 'sh skillsync.sh --pull' 以仓库为准覆盖运行时。"
      exit 1
    fi
    echo
    echo "全部一致。"
    ;;
  --pull)
    echo "== 从仓库拉回运行时 =="
    for s in $FRAMEWORK_SKILLS; do pull_one "$s"; done
    ;;
  *)
    echo "== 同步技能：运行时 → 仓库 =="
    echo "-- 框架仓 --"
    for s in $FRAMEWORK_SKILLS; do copy_one "$s" "$AS/skills"; done
    echo "-- 业务仓 --"
    for s in $RC_SKILLS; do copy_one "$s" "$RC/skills"; done
    echo
    echo "完成。记得分别在两个仓库里 commit + push。"
    ;;
esac
