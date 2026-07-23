# Offline Audio Transcriber

这是我处理前两段录音时所用流程的可复现版本：

1. 使用 `ffmpeg` 把 M4A 转换成 **16 kHz、单声道、16-bit PCM WAV**。
2. 按固定长度切片，避免长音频一次性识别失败，也便于单独重跑低质量区间。
3. 使用 `pywhispercpp` 调用本地 `whisper.cpp` 多语种模型，指定中文、beam search 和行业术语提示。
4. 给每个切片加回全局时间偏移，合并为完整时间轴。
5. 输出含时间戳 TXT、SRT、Markdown 和结构化 JSON。
6. 最终“整理文字稿”再做语义复核：去无意义停顿、补标点、校正技术术语、标注听不清处。

> 注意：代码能复现机器转写部分。此前交付的“整理文字稿”包含人工/语言模型语义校正；说话人标签也不是声纹识别结果。

> **语言限制：本工具仅支持标准普通话。对“塑料普通话”（口音明显、不标准的普通话）
> 的支持实测效果很差。** 方言、多人重叠说话、强混响和背景噪声同样会显著降低准确率。

## Windows GUI 便携版

已经提供 Windows 桌面界面，支持：

- 浏览选择音频/视频与输出目录；
- 使用随包内置的多语种 `small` 模型，也可选择其他本地 GGML 模型；
- 设置中文/英文/日文/自动识别、切片时长、CPU 线程数和 Beam；
- 加载自定义术语表；
- 在界面中查看转写日志，并直接打开输出目录。

发布包入口：

```text
dist\OfflineAudioTranscriber\OfflineAudioTranscriber.exe
```

整个 `OfflineAudioTranscriber` 文件夹是一个便携应用。复制到其他 64 位 Windows
电脑后直接双击 EXE，无需安装 Python、ffmpeg 或 Whisper 模型。不要只复制 EXE，
因为 `_internal` 目录包含运行时、`ffmpeg` 和约 465 MiB 的离线模型。

构建环境需要 Python 3.10+ 和完整的 `models\ggml-small.bin`。构建脚本会通过
`imageio-ffmpeg` 获取便携式 FFmpeg。在 PowerShell 中运行：

```powershell
.\build_windows.ps1
```

脚本会安装构建依赖、生成便携目录、执行冻结程序自检，并生成：

```text
dist\OfflineAudioTranscriber-windows-x64.zip
```

若依赖已经安装，可使用 `.\build_windows.ps1 -SkipInstall`。发布包中的第三方许可
说明见 `THIRD_PARTY_NOTICES.txt`。

## 1. 环境

- Python 3.10 或更高版本
- ffmpeg / ffprobe
- Windows、macOS 或 Linux

### Windows 安装 ffmpeg

```powershell
winget install Gyan.FFmpeg
```

关闭并重新打开 PowerShell，然后确认：

```powershell
ffmpeg -version
python --version
```

### macOS

```bash
brew install ffmpeg
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y ffmpeg
```

## 2. 安装 Python 依赖

```bash
python -m venv .venv
```

Windows：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
```

macOS / Linux：

```bash
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
```

本项目不再直接使用 `pywhispercpp` 的内置模型下载器。脚本会先把模型下载到项目的 `models` 目录，并提供：

- 网络中断自动重试；
- `.part` 文件断点续传；
- 已知官方模型的 SHA-1 校验；
- 本地 `ggml-*.bin` 路径加载。

`pywhispercpp` 仍负责模型推理。

## 3. 最简单的运行方式

中文会议录音建议至少使用 `small`：

```bash
python transcribe_audio.py "基建AI砸碎聊天框.m4a" `
  --model small `
  --language zh `
  --output-dir output `
  --glossary glossary.example.txt
```

Windows PowerShell 也可以直接运行：

```powershell
.\run_windows.ps1 -InputFile "D:\audio\基建AI砸碎聊天框.m4a" -Model small
```

## 4. 与此前实际运行参数对应的命令

此前为适应受限运行环境，主要使用本地 `ggml-tiny.bin`，大致参数如下：

```bash
python transcribe_audio.py "录音.m4a" `
  --model "/path/to/ggml-tiny.bin" `
  --language zh `
  --chunk-seconds 60 `
  --beam-size 5 `
  --threads 6 `
  --glossary glossary.example.txt
```

`tiny` 速度快，但中文专业录音误识别较多。普通电脑优先尝试：

- `base`：更快、精度一般；
- `small`：中文精度和速度较平衡；
- `medium`：更准确，但内存和耗时明显增加；
- 本地路径：例如 `D:\models\ggml-small.bin`。

不要使用带 `.en` 的模型；它们是英文专用模型。

## 5. 输出文件

假设输入为 `recording.m4a`：

- `recording_raw_timestamps.txt`：逐段时间戳、文本和置信度；
- `recording_raw.srt`：字幕文件；
- `recording_raw.md`：不含时间戳，按停顿、句末标点和篇幅组织为自然段落；
- `recording_segments.json`：供程序二次处理的结构化结果。

## 6. 行业术语提示

编辑 `glossary.example.txt`，一行一个词。例如：

```text
DCP Lite
GIM Viewer
OpenCode
Vibe Research
输变电工程
数字孪生
```

这些词会拼成 Whisper 的 `initial_prompt`，并传给每一个音频切片。它只能提高倾向性，
不能保证正确。建议按录音主题编辑，只保留实际可能出现的词；无关词过多可能反而干扰
模型判断。

## 7. 如何生成“整理文字稿”

机器识别后，把 `*_raw_timestamps.txt` 连同 `polish_prompt.md` 交给语言模型或人工编辑。需要重点检查：

- 人名、公司名、英文缩写；
- 数字、单位、否定词；
- 识别置信度较低的区间；
- 切片边界附近是否丢字或重复；
- 双人对谈中的说话人切换。

本项目没有伪造声纹级说话人识别。若确实需要 diarization，应另接 Pyannote 等说话人分离方案，并人工复核。

## 8. 常见问题

### 首次运行会下载模型

按模型名运行时，本项目会把模型保存到 `./models`，下载中断后可续传。也可以传入已经下载好的 GGML 模型路径，完全离线运行。

### 中文输出很差

依次尝试：

1. 把 `tiny` 改成 `small` 或 `medium`；
2. 检查录音是否过小、混响严重；
3. 扩充 glossary；
4. 对失败时间段单独截取并重跑；
5. 不要用 `.en` 模型。

### 想保留切片用于排错

增加：

```bash
--keep-temp
```

输出目录会保留标准化 WAV 和所有切片。

## 9. 模型下载中断

旧版本若出现以下错误：

```text
requests.exceptions.ChunkedEncodingError
IncompleteRead(... bytes read, ... more expected)
```

说明模型文件尚未下载完成，与音频格式和转写参数无关。旧版 `pywhispercpp` 下载器在异常后会删除未完成文件，因此之前下载的部分通常不能继续使用。

更新后的脚本会下载到：

```text
models\ggml-small.bin.part
```

发生中断时不要删除 `.part` 文件，直接重新运行原命令即可续传：

```powershell
python transcribe_audio.py "电网GIM模型的数字考古（含展望）.m4a" `
  --model small `
  --models-dir models `
  --download-retries 20 `
  --download-timeout 120 `
  --language zh `
  --output-dir output `
  --glossary glossary.example.txt
```

也可以先用 Windows 自带的 `curl.exe` 单独下载：

```powershell
.\download_model_windows.ps1 -Model small -ModelsDir ".\models" -Retries 20
```

下载完成后明确使用本地模型：

```powershell
python transcribe_audio.py "电网GIM模型的数字考古（含展望）.m4a" `
  --model ".\models\ggml-small.bin" `
  --language zh `
  --output-dir output `
  --glossary glossary.example.txt
```

`small` 模型的预期 SHA-1 为：

```text
55356645c2b361a969dfd0ef2c5a50d530afd8d5
```

手动校验：

```powershell
Get-FileHash ".\models\ggml-small.bin" -Algorithm SHA1
```

如果访问模型站点需要代理，可先设置 PowerShell 环境变量，例如：

```powershell
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
```

也可以将兼容的模型下载地址设置为：

```powershell
$env:WHISPER_MODEL_BASE_URL = "https://your-model-host/resolve/main"
```

该地址下应存在形如 `ggml-small.bin` 的文件。
