---
name: corpus-en
description: 英语作文语料库维护。用户发来版书图片/作文/批改版，或说「整理英语语料」「英语复习」时使用。按 What→How→Effect→Value 提炼，产出候选由用户勾选，维护遗忘曲线复习队列。
---

> **开工前先读 `/var/minis/memory/GLOBAL.md`**，读完再动手。

# corpus-en

英语作文语料库的执行 SOP。**规范源是 vault 里的 `PROMPT.md`，那是判定标准，不要改。**

```bash
CORPUS=/var/minis/mounts/7A84464D-AA03-41E8-A2F3-C38F2CA047D0/知识库/语料库
INTAKE=/var/minis/mounts/Note/En/收集      # 投料口
```

## 两条铁律

1. **选稿制**：AI 出候选，用户打勾，**未勾的不收**。不要自作主张全塞进 corpus。
2. **vault 根目录禁止放任何文件**，所有产出留在 `语料库/` 之内。

## 目录

```
语料库/
├── PROMPT.md           规范（不改）
├── WORKFLOW.md         详细 SOP
├── INDEX.md            总索引 + 复习 Queue
├── corpus/             01-科技与城市生活.md … 00-跨主题句式库.md
├── proposals/          ← 候选落在这里，等用户打勾
├── REVIEW/             due.sh + recall-queue.md
└── images/             原始图片、转换后的页面图
```

## 流程

### 1. 扫投料

```bash
ls "$INTAKE"                       # 只看根目录，跳过 已处理/
```

处理完把原文件 `mv` 到 `收集/已处理/<材料名>/`，避免重复处理。

### 2. 整理：固定用 What→How→Effect→Value

| 列 | 含义 |
|---|---|
| What | 具体事例/现象 |
| How | 如何实现、运作 |
| Effect | 直接效果 |
| Value | 更高层的意义 |

用户基础较好，**不要只收基础词汇，也不要追求华丽生僻**。核心是「少而精、可迁移、可记忆、可调用」。

### 3. 出候选

写到 `proposals/<日期>-<主题>.md`，每条一行，前面留 `- [ ]` 让用户勾：

```markdown
- [ ] shared bikes ｜ people unlock bikes through mobile apps ｜ 
      make short-distance travel more convenient ｜ promote greener urban transportation
```

### 4. 用户勾选后并入 corpus

只搬打勾的。并入时更新对应主题文件，并把一级标题改成最新修改日期（如「科技与城市生活 2026.11.2」）。

### 5. 维护复习队列

`REVIEW/recall-queue.md` 表格格式不能改，`due.sh` 靠它解析：

```
| unit_id | type | 来源文件 | 单元 | learned | interval | next_recall | last_result | notes |
```

间隔：1d → 3d → 7d → 15d → 30d → 毕业。自评 solid / fuzzy（同间隔重排）/ blank（重置 1d）。

### 6. 复习（手动触发）

```bash
TODAY=$(date +%F) sh "$CORPUS/REVIEW/due.sh"
```

- 每次给 **3 条**
- **抽卡背诵：直接给中英对照，用户自己背**
- 下次抽到同一条时，先让默写，再给对照
- **不要翻译题、不要改错题**

## 汇报

只报数字：处理了几份材料、出了几条候选、队列从 N 变 M。不要复述整理了哪些句子。
