# 私人工具箱

这是一个用于收纳个人桌面小工具的仓库。工具默认优先考虑本地运行、隐私和简单使用，
不会主动上传用户文件。

## 工具列表

| 工具 | 用途 | 平台 |
| --- | --- | --- |
| `offline_audio_transcriber` | 使用本地 Whisper 模型把音频转换为文字稿和字幕 | Windows 64 位 |

## offline_audio_transcriber

离线音频转写桌面工具。发布包已经包含 Python 运行环境、FFmpeg、Whisper 原生库和
多语种 `small` 模型，解压后可以直接运行，不要求目标电脑安装开发环境。

### 语言支持限制

**本工具仅支持标准普通话。对“塑料普通话”（口音明显、不标准的普通话）的支持实测效果
很差。** 方言、多人重叠说话、强混响、远距离录音和背景噪声也会明显降低准确率。

工具输出属于机器识别结果。人名、专业术语、数字、单位和否定词必须人工复核，不能将
输出直接用于需要高准确性的正式材料。

### 使用便携版

1. 解压 `offline_audio_transcriber/dist/OfflineAudioTranscriber-windows-x64.zip`。
2. 保留解压后的完整目录，不要只复制其中的 EXE。
3. 双击 `OfflineAudioTranscriber.exe`。
4. 选择音频、输出目录和术语表，然后开始转写。

程序会生成：

- `*_raw.md`：不含时间戳、按自然段落组织的阅读版机器稿；
- `*_raw_timestamps.txt`：含逐段时间戳和置信度的复核版本；
- `*_raw.srt`：字幕文件；
- `*_segments.json`：包含时间轴和置信度的结构化数据。

### 术语表

`offline_audio_transcriber/glossary.example.txt` 每行填写一个术语。程序会过滤空行和
注释，并将术语作为 Whisper 的 `initial_prompt` 传给每个音频切片。

术语表的作用是提高模型选择这些词的倾向，不是强制替换词典。术语与实际录音内容无关、
模型太小、发音不清或口音较重时，仍可能完全不命中。建议复制示例文件并按每段录音的
主题删改，只保留确实可能出现的词。

### 从源码构建

进入工具目录后运行：

```powershell
cd .\offline_audio_transcriber
.\build_windows.ps1
```

更完整的安装、命令行使用和模型说明见
[`offline_audio_transcriber/README.md`](offline_audio_transcriber/README.md)。

## 隐私与仓库规则

仓库的 `.gitignore` 默认忽略音频、视频、模型、输出文字稿和构建产物，避免将私人录音
及大文件误提交到 Git。提交前仍应执行 `git status` 人工确认。
