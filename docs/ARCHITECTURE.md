# 两个仓库的关系

`agent-system`（框架）与 `reading-collector`（插件）是**分工关系**，不是替代关系。

```
┌──────────────────────────────────────────────┐
│  agent-system —— 框架                        │
│                                              │
│  决定「谁该干活」：路由、并发、状态、体检      │
│  不含任何具体业务代码                          │
└──────────────────┬───────────────────────────┘
                   │ 按能力声明调度
     ┌─────────────┼─────────────┬─────────────┐
     ▼             ▼             ▼             ▼
┌─────────┐  ┌──────────┐  ┌─────────┐  ┌──────────┐
│reading- │  │video-    │  │github-  │  │corpus-   │
│collector│  │notes     │  │maintainer│ │en / zh   │
└─────────┘  └──────────┘  └─────────┘  └──────────┘
  业务仓库                   业务能力（技能）
```

## 一句话划分

| | agent-system | reading-collector |
|---|---|---|
| **管什么** | 谁该干活、怎么调度 | 怎么把一篇内容整理成笔记 |
| **有没有业务逻辑** | 没有 | 有，全是 |
| **能不能单独用** | 能（路由 + 体检） | 能（直接跑脚本） |
| **依赖方向** | 不依赖业务仓库 | 不依赖框架 |

**关键**：框架不认识「公众号」「B站」，它只认识「谁声明了处理这类来源」。

---

## 为什么拆成两个仓库

### 1. 耦合会拖慢两边

如果把业务代码塞进框架：

- 想用框架做别的事（比如记账、日程），得先剥掉一半代码
- 改笔记格式时，框架也要重新发布

拆开之后，`reading-collector` 换掉，框架一行不用动。

### 2. 复用路径不一样

- **框架**：适合任何「多个技能需要统一调度」的场景
- **业务**：只适合「待办 → Obsidian」这一种工作流

### 3. 对外表达清楚

别人看 `agent-system` 想知道「怎么让多个 agent 协作」，
看 `reading-collector` 想知道「怎么把链接整理进知识库」。
混在一起，两边都说不清。

---

## 它们怎么连起来

靠 `workers/*.json` 这一层**能力声明**，不是代码 import。

### 框架侧

```json
{
  "name": "reading-collector",
  "skill": "/var/minis/skills/reading-collector/SKILL.md",
  "triggers": ["整理", "收集", "待办"],
  "sources": ["todo"],
  "prompt": "你是 reading-collector Worker……"
}
```

框架只读这份声明：什么词触发、什么来源归它、子代理该拿什么指令。
**它不知道 `inbox.py` 长什么样，也不需要知道。**

### 业务侧

`reading-collector` 提供：

- `scripts/*.py` —— 实际干活的程序，可独立运行
- `skills/reading-collector/SKILL.md` —— 操作手册，子代理照着做

### 连接点只有三处

| 连接 | 形式 | 换了怎么办 |
|---|---|---|
| 触发词 | `workers/reading-collector.json` 的 `triggers` | 改 JSON，不动代码 |
| 技能路径 | 同文件的 `skill` 字段 | 换宿主时改路径 |
| 子代理指令 | 同文件的 `prompt` | 改 JSON |

---

## 单独用 vs 一起用

### 只用 reading-collector

```bash
cd reading-collector
python3 scripts/inbox.py              # 读待办
python3 scripts/archive.py --title …  # 归档
```

不需要框架。适合只想整理链接的人。

### 只用 agent-system

```bash
cd agent-system
python3 router/router.py --text "整理收集"   # 路由
```

没有业务 Worker 也能跑：路由、状态、体检都独立可用。
自己写几个 `workers/*.json` 就能接别的活。

### 一起用（推荐）

框架负责「什么时候谁来干」，业务负责「具体怎么干」。

```
你说「整理收集」
   │
   ▼  router.py 判断
workers/reading-collector.json
   │
   ▼  子代理照 SKILL.md 执行
reading-collector/scripts/inbox.py → archive.py
   │
   ▼  回写状态
state.py 记一笔 done
```

---

## 技能文件的三份副本

这是个实际的坑：同一个 `SKILL.md` 存在三个地方。

| 位置 | 作用 |
|---|---|
| `/var/minis/skills/<name>/` | **运行时真相**，agent 实际读的 |
| `agent-system/skills/<name>/` | 框架仓随代码走的副本 |
| `reading-collector/skills/<name>/` | 业务仓随代码走的副本 |

### 为什么不全删掉只留一份

- 仓库要能独立克隆运行，不能依赖你本机的技能目录
- 但运行时那份才是 agent 读的，改了仓库不改运行时，行为不会变

### 所以加了同步脚本

```bash
cd agent-system
sh scripts/skillsync.sh           # 运行时 → 两个仓库
sh scripts/skillsync.sh --check   # 只核对，不一致报错
sh scripts/skillsync.sh --pull    # 仓库 → 运行时（换机器时用）
```

**约定：运行时是真相。** 改技能先改 `/var/minis/skills/`，再同步出去。

### 收录范围不同

- 框架仓收录**全部 7 个**技能（含 router、管家这些元角色）
- 业务仓只收录**自己的 3 个**（reading-collector、video-notes、github-maintainer）

---

## 加新业务能力时怎么选

| 情况 | 放哪 |
|---|---|
| 给现有的收集流程加一个来源（比如 RSS） | 改 `reading-collector` |
| 加一个完全不同的业务（记账、日程） | 新建业务仓，在框架里加一份 `workers/*.json` |
| 改路由策略、加并发控制 | 改 `agent-system` |
| 加一个新 Worker 的能力声明 | 框架仓 `workers/` + 对应技能 |

**判断标准**：需要写具体业务代码 → 业务仓；只涉及调度 → 框架仓。

---

## 常见误解

**「写了 Worker 就要改框架代码」** —— 不用。加一个 `workers/*.json` 就够了。

**「reading-collector 依赖 agent-system 才能跑」** —— 不依赖。它自己是完整的 CLI 工具集。

**「改技能改仓库就行」** —— 不行。运行时那份才是 agent 读的，要用 `skillsync.sh` 推过去。

**「两个仓库要版本对齐」** —— 不需要。它们通过 `workers/*.json` 松耦合，只要技能路径对得上就行。
