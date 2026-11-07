---
name: video-notes
description: 把视频/播客链接转成逐字稿并写成中文结构化笔记。yt-dlp 抓音轨与章节，ffmpeg 切片，本地语音识别转写，agent 自己总结。用户发来视频链接要总结/转写/整理笔记时使用。
---

# video-notes

**不依赖第三方 API、不消耗额度、无需登录。**字幕需登录的站点（如 B 站）走音频转写绕开。


> **开工前先读 `/var/minis/memory/GLOBAL.md`**（环境事实、协作铁律、已知坑），读完再动手。

## 前置

仓库根目录记为 `RC`。脚本：`$RC/scripts/transcribe.py`

语音识别后端二选一（安装脚本会自动探测）：

| 后端 | 适用 | 说明 |
|---|---|---|
| `apple` | macOS / iOS | `apple-speech` 命令，零配置免费 |
| `whisper` | 任意平台 | `pip install faster-whisper`，x86_64 可用 |

## 录音文件

用户发的语音/会议录音直接转写，不需要是网络链接：

```bash
python3 "$RC/scripts/transcribe.py" "/path/to/录音.m4a" --locale zh-CN
```

支持 `m4a / mp3 / wav / aac / flac / ogg / opus / amr / 3gp / webm`，
内部自动转成 16k 单声道再切片。**本地文件默认按中文识别**（个人录音多为中文），
英文材料显式加 `--locale en-US`。

> 手机上的录音一般要先放进 `/var/minis/attachments/` 或共享目录才能被沙箱读到。

## 用法

```bash
python3 "$RC/scripts/transcribe.py" "<URL>" --locale en-US -o out.json
```

后台跑长视频并看进度：

```bash
nohup python3 "$RC/scripts/transcribe.py" "<URL>" --locale en-US \
  > /tmp/vn.log 2>&1 &
# 进度看 [4/4] 后面的 N/M
```

## 硬性约束

1. **`--locale` 必须是原声语言**。英文讲座 `en-US`，中文 `zh-CN`。选错输出乱码，不是「效果差」是「不可用」。
2. **识别必须串行**。并发会被限流——实测 6 路并发时 61 片只成功 1 片。串行 + 间隔 + 重试可以 61/61。
3. `chunk_seconds` 默认 45。太短增加重启开销，太长影响对齐精度。
4. 覆盖不足一半会自动告警，通常是限流，调大 `gap` / `retries` 重跑。

## 产出结构

```json
{ "title": "...", "uploader": "...", "duration": 2717,
  "chapters": [{"t": 0, "title": "开场"}],
  "transcript": ["[00:00] 文本…", "[00:45] 文本…"] }
```

## 出笔记

逐字稿是原料，笔记由 agent 写：

```
# 标题
> 讲者 / 课程 / 时长 / 来源
## 一句话结论
## 分章要点      ← 按 chapters 时间轴
## 关键概念速查   ← 表格
## 可直接抄的做法
## 金句          ← 原文 + 译文 + 时间戳
```

写完后用 `archive.py` 归档。

## 已知不兼容

`ctranslate2`（faster-whisper 的推理内核）只发布 glibc 版本，**Alpine/musl 上导入即段错误**，
并且它的动态库被加载后会让同进程后续的 `import av` 也崩溃。musl 用户请改用 `apple` 后端。
