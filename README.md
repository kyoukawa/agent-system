# agent-system

多 Agent 路由系统（Super-agent 架构的裁剪实现）。**Router 只分派，不上手。**
纯 Python 3 标准库，无第三方依赖，可在 iOS / iSH / Alpine (musl) 上直接跑。

参照 Obsidian 笔记《Super-agent 设计探索》。原文讲的是跨会话**常驻进程**；
本机（iOS + iSH）没法常驻，所以裁剪成「一次调用跑一轮」——由 agent 手动驱动，
但五个核心模块（Router / Watchdog / 事件层 / 状态层 / Worker 声明）的设计思想全部保留。

---

## 零、它在整个系统里的位置

**这是一个框架仓，不含业务逻辑。** 它决定「谁该干活」，不决定「活怎么干」。

```
┌────────────────────────────────────────────────────┐
│  agent-system（本仓库）—— 框架                       │
│  路由 · 并发 · 状态 · 体检                          │
└────────────────────┬───────────────────────────────┘
                     │ 只读 workers/*.json 的能力声明
   ┌─────────┬───────┴───────┬────────────┬──────────┐
   ▼         ▼               ▼            ▼          ▼
reading-   video-        github-      corpus-en   corpus-zh
collector  notes         maintainer
   │
   └── 业务实现见另一个仓库
       github.com/kyoukawa/reading-collector
```

### 与 reading-collector 的分工

| | agent-system | reading-collector |
|---|---|---|
| 管什么 | 谁该干活、怎么调度 | 一篇内容怎么变成笔记 |
| 业务代码 | **没有** | 全是 |
| 能单独用吗 | 能 | 能 |
| 依赖对方吗 | 不依赖 | 不依赖 |

**连接点只有三处**，全在 `workers/reading-collector.json` 里：
`triggers`（什么词触发）、`skill`（手册路径）、`prompt`（子代理拿到的指令）。
框架不知道 `inbox.py` 长什么样，也不需要知道。

> 📖 详细说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：
> 为什么拆开、怎么连起来、技能副本怎么同步、加新业务时该放哪个仓。

---

## 一、为什么是这个结构

```
用户说话
   │
   ▼
┌─────────────────────────────────────────────────────┐
│ events.py  Fast Path（正则）→ 命中就直接定候选        │
│            没把握 / 太长 → 标 needs_model，交回模型   │
│            Inline & Spill：长文落盘，事件只留预览      │
└─────────────────────────────────────────────────────┘
   │  规范化事件 {id, source, source_id, thread, text}
   ▼
┌─────────────────────────────────────────────────────┐
│ router.py  0) 元角色请求？→ 不路由                     │
│            1) 显式 thread 已有归属？→ 直通 continue    │
│            2) 按 triggers 打分 → route / ambiguous    │
│               / unrouted（绝不瞎猜、绝不硬接）         │
└─────────────────────────────────────────────────────┘
   │  plan: worker / mode / task / why
   ▼
   Worker 子代理（每个一条，上下文隔离）
   │  返回 状态 / 产出路径 / 一句话结论
   ▼
┌─────────────────────────────────────────────────────┐
│ state.py   原子写 + 链式召回（parents 图逐跳展开）     │
│ watchdog.py 僵尸 / 产出缺失 / 日志膨胀 / 旧 bug 痕迹   │
└─────────────────────────────────────────────────────┘
```

**分层理由**

| 想法 | 落地方式 |
|---|---|
| 存算分离 | `state.py` 只负责存，一行路由判断都没有；判断全在 `router.py` |
| 上下文隔离 | Router 为每条 plan 各开一个子代理，Worker 之间互不可见 |
| 成本分级 | Fast Path 是纯正则，零模型调用；只有拿不准才上升 Slow Path |
| 长文不撑爆上下文 | 超过 1200 字符自动 spill 到 `triggers/spill/` |
| 不硬接 | 没命中任何 Worker → `unrouted`，汇报给用户问；同分 → `ambiguous`，列候选让用户选 |

---

## 二、目录

```
agent-system/
├── README.md
├── .gitignore
├── router/
│   ├── router.py      路由：只分派不上手（CLI 入口）
│   ├── watchdog.py    猎犬：僵尸任务 / 产出缺失 / 日志膨胀，能自愈的自愈
│   ├── validate.py    管家工具：校验 Worker 定义健康度
│   └── scaffold.py    管家工具：生成新 Worker 骨架
├── triggers/
│   ├── events.py      事件层：Fast/Slow Path + Inline & Spill
│   └── spill/         长文本落盘（不入库）
├── state/
│   ├── state.py       状态层：原子写 + 链式召回
│   ├── tasks.json     任务表（运行时生成）
│   ├── threads.json   线程表（运行时生成）
│   └── events.jsonl   事件流水（运行时生成）
├── tests/
│   └── selftest.py    冒烟测试（15 项，纯标准库，可进 CI）
├── skills/            七份技能手册副本（Router / 管家 / 五个 Worker）
│   ├── agent-router/SKILL.md
│   ├── system-steward/SKILL.md
│   └── <worker>/SKILL.md
├── examples/
│   └── events.jsonl   示例事件流（含直通 / 噪音 / 歧义三种情况）
└── workers/           五个 Worker 的能力声明
    ├── reading-collector.json
    ├── video-notes.json
    ├── github-maintainer.json
    ├── corpus-en.json
    └── corpus-zh.json
```

Worker 的**操作手册**在技能目录，不在本仓库：`/var/minis/skills/<name>/SKILL.md`。
本仓库存的是**能力声明**（触发词、来源、prompt），技能存的是**怎么做**。
本仓库存两份东西：**能力声明**（`workers/*.json`：触发词、来源、prompt）
和**操作手册副本**（`skills/<name>/SKILL.md`：具体怎么做）。

运行时技能要从 `skills/` 装到 agent 的技能目录（Minis 上是 `/var/minis/skills/`）：

```bash
cp -r skills/* /var/minis/skills/
```

`workers/*.json` 里的 `skill` 字段指向运行时的路径，换宿主时改这一处即可。

---

## 三、五个 Worker

| Worker | 干什么 | 技能 |
|---|---|---|
| `reading-collector` | 待办「阅读收集」分组里的链接 → Obsidian 笔记 → 勾完成 → 可选镜像 Notion | `reading-collector` |
| `video-notes` | 视频 / 播客 → yt-dlp + ffmpeg + apple-speech → 逐字稿 → 中文结构化笔记 | `video-notes` |
| `github-maintainer` | git 提交、推送、建仓、改名、release、issue/PR | `github-maintainer` |
| `corpus-en` | 英语作文语料库（What→How→Effect→Value） | `corpus-en` |
| `corpus-zh` | 语文作文语料库（老师六维） | `corpus-zh` |

> ⚠️ **两个语料库判据不同，绝不能混用。**
> 英语看 What→How→Effect→Value，重「少而精、可迁移、可调用」；
> 语文看老师六维（概念界定→段首句→机制→论据→思路→深化），只收「我的原句 / 老师的机制 / 避坑」三类。
> 两者都是**选稿制**（AI 出候选、用户打勾），复习都是**手动触发、每次 3 条**。

**元角色**（不参与路由，也不该有 Worker 定义）：
`agent-router`（统一入口）、`system-steward`（架构管家）、`skill-creator`、`guided-learning`。
`validate.py` 会把给它们写 worker 定义的情况报成 error（E5）。

---

## 三·五、三十秒验证

```bash
python3 tests/selftest.py                     # 15 项，应全绿
python3 router/validate.py                    # error 必须为 0
python3 router/router.py --events examples/events.jsonl
```

示例事件流会同时演示三件事：

```
收到 5 条候选，已分派 4 项
  · [新建] reading-collector ← 关键词命中 3 个
  · [新建] github-maintainer ← 关键词命中 1 个
  · [续接] github-maintainer ← 该 Thread 已有归属，直通
  · [新建] video-notes ← 关键词命中 4 个
  ? 未归属：英语 语文 作文
```

- **直通**：pr-42 的第二条没再让 Router 判断
- **噪音过滤**：「收到」根本没进候选
- **歧义不猜**：两个语料库都能接，报未归属而不是随便选一个

> `--dry-run` 不写状态，所以直通不会生效——想看直通要去掉这个参数。

---

## 四、用法

```bash
AS=/var/minis/workspace/agent-system
```

### 1. 看全局状态

```bash
python3 $AS/state/state.py status            # 任务统计 + 未完成清单
python3 $AS/state/state.py list              # 全部任务
python3 $AS/state/state.py threads           # 线程归属（直通靠它）
python3 $AS/state/state.py recall t-xxx      # 链式召回：这条任务的整条因果链
python3 $AS/state/state.py events 20         # 最近 20 条事件流水
```

### 2. 路由

```bash
# 一句话当成 user 事件
python3 $AS/router/router.py --text "帮我整理收集一下待办" --dry-run

# 批量结构化事件
python3 $AS/router/router.py --events events.jsonl

# 显式声明线程（这样后续同线程事件会直通）
python3 $AS/router/router.py --text "顺便推上去" --thread rc-2026-11

# 把 worker 的 prompt 一并带出来（准备开子代理时用）
python3 $AS/router/router.py --text "转写这个视频" --with-prompt --json
```

输出：

```
派给     reading-collector    (1.90)  thread=user-untracked  [auto]
        为什么: 命中 reading-collector（整理收集）
        任务: t-c184112a1e27  mode=new
```

五种动作：

| action | 含义 | 该怎么办 |
|---|---|---|
| `route` | 唯一最高分 | 按 `worker` 开子代理 |
| `continue` | 显式 thread 已归属 | 续接原 Worker，`parents` 自动挂上上一条 |
| `ambiguous` | 多个 Worker 同分 | **问用户选哪个**，不要自己挑 |
| `unrouted` | 没有任何命中 | **报给用户**，不要硬接 |
| `meta` | 加 Worker / 体检 / 改配置 | 交给 `system-steward` |

有 `ambiguous` 或 `unrouted` 时退出码为 **3**，方便脚本感知。

### 3. 分派给 Worker

每条 plan 各开一个子代理（互相隔离）：

```
subagent_task(
  task = worker.prompt + "\n\n具体任务：<事件摘要>\n\n"
         "执行完返回：状态(成功/失败)、产出路径、一句话结论。不要复述过程。"
)
```

多个 Worker 并行时**一次性全部发起**，不要排队等第一个。

### 4. 写回状态

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "/var/minis/workspace/agent-system/state")
import state
state.update_task("t-c184112a1e27", status="done", output="/绝对/路径.md")
PY
```

状态只有 `pending / running / done / failed` 四种，写别的会抛 `ValueError`。
`done` 必须带**绝对路径**的 `output`，否则 watchdog 会报 `no_output` / `missing`。

### 5. 收尾体检

```bash
python3 $AS/router/watchdog.py            # 顺手自愈僵尸任务
python3 $AS/router/watchdog.py --dry-run  # 只看不改
python3 $AS/tests/selftest.py             # 冒烟测试
```

---

## 五、加一个新 Worker

```bash
# 1. 查重（很重要：已经有人干的事不要新建）
python3 $AS/router/router.py workers
python3 $AS/router/validate.py

# 2. 生成骨架（先 dry-run 看）
python3 $AS/router/scaffold.py finance-tracker \
  --purpose "记录日常收支并做月度复盘" \
  --triggers "记账,收支,月度复盘" --sources user --dry-run

# 3. 写实 skills/finance-tracker/SKILL.md 里的 TODO（输入/流程/产出/铁律/汇报）

# 4. 校验：error 必须清零
python3 $AS/router/validate.py finance-tracker

# 5. 试路由：确认派给它，且没抢走别人的活
python3 $AS/router/router.py --text "这个月收支记一下" --dry-run
```

---

## 六、Worker 定义字段

```json
{
  "name": "reading-collector",            // 必须与文件名、SKILL.md front-matter name 一致
  "purpose": "一句话职责",
  "skill": "/var/minis/skills/reading-collector/SKILL.md",
  "enabled": true,                        // false = 软停用，router 跳过
  "triggers": ["整理收集", "处理待办"],    // 关键词，越长权重越高
  "sources": ["todo", "url", "user"],
  "tools": ["shell_execute", "file_read"],
  "model": "default",
  "prompt": "给子代理的第一段话：身份 + 依据哪份 SKILL + 流程概要 + 铁律 + 返回什么"
}
```

**prompt 写法**：说清身份和依据的 SKILL，说清**返回什么**，但**不复述** SKILL 里已有的细节。
`validate.py` 的 I1 会检查有没有写「返回」。

---

## 七、校验规则一览（validate.py）

| 级别 | 代码 | 检查 |
|---|---|---|
| error | E1 | 必填字段缺失（name / purpose / triggers / sources / prompt） |
| error | E2 | `skill` 指向的文件不存在 |
| error | E3 | SKILL.md front-matter 的 `name` 与 worker `name` 不一致 |
| error | E4 | `name` 与文件名不一致 |
| error | E5 | 元角色居然有 worker 定义 |
| error | E6 | triggers 为空或不是数组 |
| error | E7 | sources / tools 不是数组 |
| error | E8 | 指定的 worker 不存在 |
| warn | W1 | 触发词被多个 Worker 认领 → 会报 ambiguous |
| warn | W2 | 非通用来源被多个 Worker 声明 |
| warn | W3 | 触发词是「整理 / 处理」这类泛词 |
| warn | W4 | 触发词太短（≤1 字符） |
| warn | W5 | triggers 内部有重复项 |
| info | I1 | prompt 没写「返回什么」 |
| info | I2 | prompt 过短（<40 字）或过长（>800 字） |
| info | I3 | prompt 没提 SKILL 文件 |
| info | I4 | 已知的语义相邻对（两个语料库、视频 vs 收集） |
| info | I5 | 有技能但没有 worker 定义 |

`--strict` 时 warn 也算失败；有 error 时退出码 1，可直接进 CI。

---

## 八、设计上的几个关键决定

### 1. `thread` 用 `source-id` 生成，不用事件 id
每个事件 id 都是新的。如果拿事件 id 当 thread，**每一条事件都会变成独立线程，直通永远命不中**。
所以 `events.derive_thread()` 统一用 `"<source>-<source_id>"`，同一来源的同一条东西落进同一线程。
`source_id` 缺失时退化为 `<source>-untracked`（确定性，仍然不用随机 id）。

### 2. 自动生成的 thread 不参与直通匹配
`thread_auto: true` 的线程只是「来源归类」，不代表「同一件事的续篇」。
**只有调用方显式声明的 thread 才走直通**，否则同一个待办分组里的两件不相干的事会被硬塞给上一个 Worker。
这一条在 `route_one()` 第一步就判断，直通优先级高于打分。
`tests/selftest.py` 里有两项专门守这条（`test_auto_thread_no_passthru`、
`test_generated_thread_with_entry_still_no_passthru`）。

### 3. 同分就报 ambiguous，不瞎猜
`top.score == second.score` 且 > 0 → `ambiguous` + 候选名单。
宁可多问一句，也不要派错人——派错人的代价是整个 Worker 白跑一遍还可能写脏数据。

### 4. `ROOT` 是仓库根，不是 `router/`
`router/watchdog.py` 曾经把 `ROOT` 算成 `router/` 的绝对路径，于是
`sys.path.insert(0, ROOT + "/state")` 指向 `router/state`，`import state` 直接崩。
现在统一 `ROOT = dirname(dirname(abspath(__file__)))`，所有模块都从这里派生路径，
并且**从任意 cwd 调用都能跑**（已实测从 `/tmp` 调用）。

### 5. Fast Path 词表与 triggers 刻意独立
`triggers/events.py` 里的 `FAST_SIGNALS` 是硬编码兜底，`workers/*.json` 的 `triggers` 是可被管家修改的配置。
两边不共享，是为了让「配置改坏了」时 Fast Path 仍有一层独立判断；代价是两处都要维护，
所以 Fast Path 只放高置信度的粗词。

### 6. Router 没有硬权限隔离
原文强调 Router 不配 Bash 工具。这里 Router 只是提示词约束 + `router.py` 里**一行执行逻辑都没有**，
没有 OS 级隔离。要更严格，需要把 router 拆成独立进程/用户。

### 7. 标准库 only
不依赖任何第三方包：JSON、re、hashlib、uuid、argparse 足够。
`/var/minis` 不支持符号链接、`chmod` 无效（执行位用 `git update-index --chmod=+x` 处理），
所以所有脚本都用 `python3 path/to/x.py` 调用，不依赖 shebang 执行位。

---

## 九、运行时文件与 .gitignore

| 文件 | 是否入库 | 说明 |
|---|---|---|
| `state/tasks.json` | ✗ | 任务表 |
| `state/threads.json` | ✗ | 线程归属 |
| `state/events.jsonl` | ✗ | 事件流水（会涨到 MB） |
| `state/*.corrupt.*` | ✗ | 损坏备份，人工检查后删 |
| `triggers/spill/` | ✗ | 长文落盘 |
| `workers/*.json.disabled` | ✗ | 软停用的 Worker |
| `workers/*.json` | ✓ | 能力声明，是唯一真相 |

状态文件损坏时 `state.py` 会自动备份为 `<file>.corrupt.<ts>` 并返回空表，**不阻塞调用方**。
检查完手动删除备份即可。

---

## 十、已知限制

- **没有常驻进程**：所有模块都是「调用一次跑一轮」。没有定时任务（Minis 的定时任务在 App 重启后会丢）。
- **子代理层由宿主提供**：`router.py` 只产出 plan，真正 fork 子代理的是调用方 agent。
- **`--locale` 类问题不在本仓库**：转写语言选错是 `video-notes` 的坑，记在它的 SKILL 里。
- **并发不安全**：`state.py` 的读-改-写在两个进程同时写时会丢更新。单 agent 串行驱动没问题；
  要并发得加文件锁（`fcntl` 在 iSH 上可用性未验证，所以没做）。
