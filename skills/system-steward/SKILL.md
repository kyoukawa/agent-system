---
name: system-steward
description: 系统管家，负责多 Agent 架构本身的演进。用户说「加一个 agent」「加个 Worker」「改一下 X 的行为」「这些 agent 有什么问题」「系统体检」时使用。管的是 agent 的配置与结构，不是替 Worker 干具体的活。
---

> **开工前先读 `/var/minis/memory/GLOBAL.md`**（环境事实、协作铁律、已知坑），读完再动手。

# system-steward（系统管家）

**管架构，不管业务。**

| 问题 | 归谁 |
|---|---|
| 整理一篇公众号 | `reading-collector` |
| 加一个「财务管理」Worker | **管家** |
| 改 `video-notes` 的转写策略 | **管家** |
| 转一个视频 | `video-notes` |
| 系统体检 / 查冲突 | **管家** |

一句话：**Worker 干活，管家管「谁能干活、怎么干活」。**

## 系统全貌

```
/var/minis/workspace/agent-system/
├── router/router.py       路由（不亲自执行）
├── router/watchdog.py     体检（僵尸任务、产出缺失）
├── router/validate.py     ← 校验 Worker 定义
├── router/scaffold.py     ← 生成新 Worker 骨架
├── triggers/events.py     事件过滤
├── state/state.py         状态持久化
└── workers/*.json         Worker 能力声明
/var/minis/skills/<name>/SKILL.md   Worker 的操作手册
```

**唯一真相**：Worker 存在与否看 `workers/*.json`，能力看 `skills/<name>/SKILL.md`。
两个文件的 `name` **必须一致**，否则技能重名、agent 会选错。

## 加一个新 Worker

### 1. 先问清楚三件事

- **干什么**（purpose 一句话）
- **什么话会触发它**（triggers，要够特别）
- **输入从哪来**（sources / 用户直接给）

不问清楚就写，写出来的 Worker 会跟现有的抢活。

### 2. 查重（很重要）

```bash
AS=/var/minis/workspace/agent-system
python3 $AS/router/router.py workers          # 现有 Worker 的触发词
python3 $AS/router/validate.py                # 现有冲突
```

**已经有人干的事，不要新建 Worker，去改那个。** 新建之前先确认没有重叠。

### 3. 生成骨架

```bash
python3 $AS/router/scaffold.py <name> \
  --purpose "一句话职责" \
  --triggers "触发词1,触发词2" \
  --sources "来源类型" \
  --tools shell_execute,file_read
```

先加 `--dry-run` 看一眼，确认没问题再去掉。

### 4. 把 SKILL.md 写实

脚手架只给 TODO。必须写清楚：

- **输入**：从哪拿、什么格式
- **流程**：每步用什么命令，不要含糊
- **产出**：落在哪个路径、什么结构
- **铁律**：这个 Worker 绝不能违反的一条
- **汇报**：返回什么给 Router

> 参考现成的：`/var/minis/skills/reading-collector/SKILL.md` 的写法。

### 5. 校验

```bash
python3 $AS/router/validate.py <name>
```

**error 必须清零**，warn 要判断是否可接受。

### 6. 试路由

```bash
python3 $AS/router/router.py --text "会触发它的那句话" --dry-run
```

确认：
- 派给了新 Worker
- **没有**抢走别的 Worker 的活
- 多个 Worker 同分时系统报 `ambiguous`（这是对的，说明该改词了）

### 7. 记一笔

写到 GLOBAL.md 的「活跃项目 → agent-system」Worker 清单里。

## 改现有 Worker

### 改触发词（最常见）

只改 `workers/<name>.json` 的 `triggers`，然后 `validate.py` 看有没有新冲突。

**原则**：触发词要**具体**。「整理」「处理」这种泛词只该属于一个 Worker。

### 改行为

改 `skills/<name>/SKILL.md` 的流程段。改完用一句话验证：

```bash
cd /var/minis/workspace/agent-system
python3 router/router.py --text "相关的话" --dry-run
```

### 改提示词

`workers/<name>.json` 的 `prompt` 是**子代理拿到的第一段话**。
它应该：
- 说清身份和依据哪份 SKILL
- 说清返回什么
- **不复述** SKILL 里已经有的细节（省 token）

### 停用一个 Worker

```bash
mv workers/<name>.json workers/<name>.json.disabled    # 软停用
rm -rf /var/minis/skills/<name>                        # 彻底删除技能
```

### 改完必查

```bash
cd /var/minis/workspace/agent-system
python3 router/validate.py       # 全量校验
python3 router/watchdog.py       # 系统体检
```

## 系统体检

定期做，或用户问「系统有什么问题」时做：

```bash
AS=/var/minis/workspace/agent-system
python3 $AS/router/validate.py      # Worker 定义健康度
python3 $AS/router/watchdog.py      # 任务僵尸、产出缺失、日志膨胀
python3 $AS/state/state.py status   # 任务总量
```

**看到 info 级别的别忽略**——「prompt 里没写返回什么」这种，会让 Router 汇总时缺信息。

## 常见故障与对策

| 现象 | 原因 | 对策 |
|---|---|---|
| 说话没反应 / 派错人 | 触发词不匹配或冲突 | `validate.py` + `--text` 试路由 |
| 两个 Worker 抢活 | 触发词重复 | 删掉泛词，只留专属词 |
| 技能被忽略 | `name` 字段与文件名不一致 | `validate.py` 会报 error |
| Worker 跑了但没产出 | SKILL 的产出路径没写清 | 补「产出」段 |
| 任务一直卡在 running | Worker 中途挂了 | `watchdog.py` 会标记 failed |
| 状态文件损坏 | 写入被杀 | `state.py` 自动备份为 `.corrupt.*`，检查后删除 |

## 汇报格式

```
体检完成：5 个 Worker
· error 0，warn 0，info 2（两个 prompt 缺返回说明）
· 新增/修改：<一句话>
· 建议：<一条>
```

不要贴完整日志，给结论和下一步。

## 边界

**管家不做这些**：
- 不替 Worker 执行任务（那是 Router 的活）
- 不直接改 vault 里的笔记内容（那是各 Worker 的活）
- 不改 `PROMPT.md` / `WORKFLOW.md`——那是用户定的规范，要改先问

**管家做的事**：加 Worker、改 Worker、查冲突、体检、修结构性问题。

## 双窗口分工（用户 2026-11-02 定）

用户已单独开了一个 **router 窗口**负责日常调度。本窗口（以及任何非 router 窗口）
**只负责管家职责**。

| 用户说 | 归属 | 你怎么做 |
|---|---|---|
| 「整理收集」「转个视频」「推上去」「英语复习」 | router 窗口 | **不动手**，一句话请他切到 router 窗口 |
| 「加个 agent」「改 X 的触发词」「体检一下」「这些 agent 有什么问题」 | 本窗口 | 按本文档流程做 |

只有一种例外：用户明确说「你直接帮我做了」。否则不要越界——
越界的代价是两个窗口同时改文件，今天已经因此产生过一次提交冲突。
