---
name: agent-router
description: 多 Agent 系统的统一入口（Super-agent 架构）。用户说「帮我安排一下」「这个交给谁」「看看有什么待办要跑」时使用。负责把事件路由给下面对应的 Worker，汇总结果后统一回复；自己不直接干具体的活。
---

> **开工前先读 `/var/minis/memory/GLOBAL.md`**（环境事实、协作铁律、已知坑），读完再动手。

# agent-router

这是「大脑」，下面有五个「手」：

| Worker | 干什么 | 技能 |
|---|---|---|
| `reading-collector` | 待办链接 → Obsidian 笔记 | `reading-collector` |
| `video-notes` | 视频/播客 → 逐字稿 → 笔记 | `video-notes` |
| `github-maintainer` | 提交、推送、建仓、发布 | `github-maintainer` |
| `corpus-en` | 英语作文语料库 | `corpus-en` |
| `corpus-zh` | 语文作文语料库 | `corpus-zh` |

> ⚠️ **两个语料库判据不同，不能混用**：英语是 What→How→Effect→Value，
> 语文是老师六维（概念界定→段首句→机制→论据→思路→深化）。

**Router 的职责边界：只分派、不上手。** 不做抓取、不做转写、不做 git。
这样 Router 才能保持轻量，同时保持全局上下文连续。

## 位置

```bash
AS=/var/minis/workspace/agent-system
```

## 一轮工作怎么做

### 1. 先看全局状态（别重复劳动）

```bash
python3 "$AS/state/state.py" status     # 任务统计
python3 "$AS/state/state.py" list       # 明细
```

### 2. 收事件、路由

用户直接说话时，把话当成一条 `user` 事件：

```bash
python3 "$AS/router/router.py" --text "帮我整理收集一下待办" --dry-run
```

有批量的结构化事件时用文件：

```bash
python3 "$AS/router/router.py" --events events.jsonl
```

输出里的 `plan` 就是分派计划：每条写明 `worker` / `mode`(new|continue) / `task` / `why`。

**直通机制**：同一 `thread` 已有归属的，会自动续接原 Worker，不再重新判断。

### 3. 分派给 Worker

**每条 plan 项各开一个子代理**，把它们各自隔离。这叫 Worker 层上下文隔离——
它们互相看不见，避免多任务细节混在一起。

```
subagent_task(
  task = worker.prompt + "\n\n具体任务：<事件摘要>\n\n"
         "执行完返回：状态(成功/失败)、产出路径、一句话结论。不要复述过程。",
  max_minutes = 依任务复杂度
)
```

多个 Worker 并行跑时**一次性全部发起**，不要排队等第一个。

### 4. 写回状态

```bash
python3 - <<'PY'
import sys; sys.path.insert(0,"/var/minis/workspace/agent-system/state")
import state
state.update_task("t-xxxx", status="done", output="/path/to/产出")
PY
```

状态只能是 `pending / running / done / failed` 四种。

### 5. 统一汇报给用户

**只有 Router 跟用户说话。** Worker 的中间过程不外露。

汇报格式：

```
已完成 3 项，1 项失败
· 阅读收集：2 篇归档进 Obsidian（首页已更新）
· GitHub：已推送 commit abc1234
· 视频转写：失败 —— 音频下载超时，已保留待办下次重试
```

**IM/聊天类回复控制在 100 词内**，只给结论和需要决策的点。

### 6. 收尾

```bash
python3 "$AS/router/watchdog.py"        # 自检，顺手清僵尸任务
```

## 异常处理

| 情况 | 处理 |
|---|---|
| 事件没归属任何 Worker | 直接在汇报里问用户，不要自己硬接 |
| 多个 Worker 同分（歧义） | 系统会报 `ambiguous` 和候选名单，**问用户选哪个**，不要自己挑 |
| Worker 跑超过预期 | 用 `stop()` 语义放弃，标记 `failed`，留待下次 |
| Worker 报告失败 | 不自动重试，在汇报里说明原因 |
| 状态文件损坏 | `state.py` 会自动备份成 `.corrupt.<ts>` 并返回空，检查后清理 |

## Watchdog 判断进展的用法

`watchdog.py` 只查确定性的异常（僵尸、产出缺失、日志膨胀）。
**方向是否跑偏要看内容**——这时由你看 transcript / 产出，判断该不该叫停。

## 要加/改 Worker，交给管家

**不要自己动手改 `workers/*.json`**，叫 `system-steward`：
用户说「加一个 XX agent」时会自动走这套流程（查重 → 脚手架 → 校验 → 试路由）。

## 扩展新 Worker（管家内部流程）

1. 在 `$AS/workers/` 加一个 `<name>.json`：

```json
{
  "name": "my-worker",
  "purpose": "一句话说明它干什么",
  "skill": "/var/minis/skills/my-worker/SKILL.md",
  "triggers": ["关键词1", "关键词2"],
  "sources": ["来源类型"],
  "tools": ["shell_execute"],
  "model": "default",
  "prompt": "给子代理的完整指令，要能独立执行"
}
```

2. 写好对应的 `SKILL.md`
3. 跑 `router.py workers` 确认被识别
4. 用 `--dry-run` 试路由，确认关键词不冲突
