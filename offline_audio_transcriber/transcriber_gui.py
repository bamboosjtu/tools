"""Windows desktop GUI for the offline audio transcriber."""

from __future__ import annotations

import argparse
import contextlib
import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from tkinter import (
    BOTH,
    DISABLED,
    END,
    LEFT,
    NORMAL,
    RIGHT,
    X,
    BooleanVar,
    DoubleVar,
    IntVar,
    StringVar,
    Tk,
    filedialog,
    messagebox,
    ttk,
)
from tkinter.scrolledtext import ScrolledText

import transcribe_audio


APP_NAME = "离线音频转写工具"
APP_DIR_NAME = "OfflineAudioTranscriber"
AUDIO_TYPES = [
    ("音频和视频", "*.m4a *.mp3 *.wav *.aac *.flac *.ogg *.wma *.mp4 *.mov *.mkv *.webm"),
    ("所有文件", "*.*"),
]


def app_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME}"


def bundled_path(relative: str) -> Path:
    return transcribe_audio.resource_root() / relative


def default_output_dir() -> Path:
    documents = Path.home() / "Documents"
    base = documents if documents.is_dir() else Path.home()
    return base / f"{APP_NAME}输出"


def open_in_explorer(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


class QueueWriter:
    def __init__(self, messages: queue.Queue[tuple[str, object]]) -> None:
        self.messages = messages
        self._buffer = ""

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buffer += text.replace("\r", "\n")
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self.messages.put(("log", line))
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self.messages.put(("log", self._buffer))
        self._buffer = ""


class TranscriberApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("850x680")
        self.root.minsize(760, 600)
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))

        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.is_running = False

        self.input_var = StringVar()
        self.output_var = StringVar(value=str(default_output_dir()))
        self.model_mode_var = StringVar(value="内置 small（推荐）")
        self.custom_model_var = StringVar()
        self.glossary_enabled_var = BooleanVar(value=True)
        default_glossary = bundled_path("glossary.example.txt")
        self.glossary_var = StringVar(
            value=str(default_glossary) if default_glossary.is_file() else ""
        )
        self.language_var = StringVar(value="zh")
        self.chunk_var = IntVar(value=60)
        self.threads_var = IntVar(value=max(1, min(8, os.cpu_count() or 4)))
        self.beam_var = IntVar(value=5)

        self._build_ui()
        self._sync_model_controls()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_messages)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill=BOTH, expand=True)

        title = ttk.Label(outer, text=APP_NAME, font=("Microsoft YaHei UI", 18, "bold"))
        title.pack(anchor="w")
        ttk.Label(
            outer,
            text="本地 Whisper 识别 · 音频不上传 · 输出 TXT / SRT / Markdown / JSON",
            foreground="#555555",
        ).pack(anchor="w", pady=(2, 16))

        files = ttk.LabelFrame(outer, text="文件", padding=12)
        files.pack(fill=X)
        self._path_row(files, "输入音频", self.input_var, self._choose_input, 0)
        self._path_row(files, "输出目录", self.output_var, self._choose_output, 1)

        settings = ttk.LabelFrame(outer, text="识别设置", padding=12)
        settings.pack(fill=X, pady=(12, 0))
        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="模型").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=5)
        self.model_combo = ttk.Combobox(
            settings,
            textvariable=self.model_mode_var,
            values=("内置 small（推荐）", "选择本地 GGML 模型"),
            state="readonly",
        )
        self.model_combo.grid(row=0, column=1, sticky="ew", pady=5)
        self.model_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_model_controls())

        ttk.Label(settings, text="本地模型").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=5
        )
        model_row = ttk.Frame(settings)
        model_row.grid(row=1, column=1, sticky="ew", pady=5)
        model_row.columnconfigure(0, weight=1)
        self.model_entry = ttk.Entry(model_row, textvariable=self.custom_model_var)
        self.model_entry.grid(row=0, column=0, sticky="ew")
        self.model_button = ttk.Button(model_row, text="浏览…", command=self._choose_model)
        self.model_button.grid(row=0, column=1, padx=(8, 0))

        ttk.Label(settings, text="术语表").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=5
        )
        glossary_row = ttk.Frame(settings)
        glossary_row.grid(row=2, column=1, sticky="ew", pady=5)
        glossary_row.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            glossary_row,
            text="启用",
            variable=self.glossary_enabled_var,
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(glossary_row, textvariable=self.glossary_var).grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(glossary_row, text="浏览…", command=self._choose_glossary).grid(
            row=0, column=2, padx=(8, 0)
        )

        advanced = ttk.Frame(settings)
        advanced.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for column in range(8):
            advanced.columnconfigure(column, weight=1 if column % 2 else 0)
        ttk.Label(advanced, text="语言").grid(row=0, column=0, padx=(0, 6))
        ttk.Combobox(
            advanced,
            textvariable=self.language_var,
            values=("zh", "en", "ja", "auto"),
            width=8,
            state="readonly",
        ).grid(row=0, column=1, sticky="w", padx=(0, 16))
        ttk.Label(advanced, text="切片秒数").grid(row=0, column=2, padx=(0, 6))
        ttk.Spinbox(advanced, from_=10, to=3600, textvariable=self.chunk_var, width=8).grid(
            row=0, column=3, sticky="w", padx=(0, 16)
        )
        ttk.Label(advanced, text="CPU 线程").grid(row=0, column=4, padx=(0, 6))
        ttk.Spinbox(advanced, from_=1, to=64, textvariable=self.threads_var, width=8).grid(
            row=0, column=5, sticky="w", padx=(0, 16)
        )
        ttk.Label(advanced, text="Beam").grid(row=0, column=6, padx=(0, 6))
        ttk.Spinbox(advanced, from_=1, to=20, textvariable=self.beam_var, width=8).grid(
            row=0, column=7, sticky="w"
        )

        actions = ttk.Frame(outer)
        actions.pack(fill=X, pady=12)
        self.start_button = ttk.Button(
            actions, text="开始转写", command=self._start, style="Accent.TButton"
        )
        self.start_button.pack(side=LEFT)
        ttk.Button(actions, text="打开输出目录", command=self._open_output).pack(
            side=LEFT, padx=(8, 0)
        )
        ttk.Button(actions, text="清空日志", command=self._clear_log).pack(side=RIGHT)

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill=X, pady=(0, 10))

        log_frame = ttk.LabelFrame(outer, text="运行日志", padding=8)
        log_frame.pack(fill=BOTH, expand=True)
        self.log = ScrolledText(
            log_frame,
            wrap="word",
            height=14,
            state=DISABLED,
            font=("Consolas", 9),
        )
        self.log.pack(fill=BOTH, expand=True)
        self._append_log("请选择音频文件，然后点击“开始转写”。")

    def _path_row(self, parent, label: str, variable: StringVar, command, row: int) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=5
        )
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(parent, text="浏览…", command=command).grid(
            row=row, column=2, padx=(8, 0), pady=5
        )

    def _choose_input(self) -> None:
        selected = filedialog.askopenfilename(title="选择音频或视频", filetypes=AUDIO_TYPES)
        if selected:
            self.input_var.set(selected)
            self.output_var.set(str(Path(selected).parent / "transcript_output"))

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(title="选择输出目录", mustexist=False)
        if selected:
            self.output_var.set(selected)

    def _choose_model(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 whisper.cpp GGML 模型",
            filetypes=(("GGML 模型", "*.bin"), ("所有文件", "*.*")),
        )
        if selected:
            self.custom_model_var.set(selected)

    def _choose_glossary(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择术语表",
            filetypes=(("文本文件", "*.txt"), ("所有文件", "*.*")),
        )
        if selected:
            self.glossary_var.set(selected)
            self.glossary_enabled_var.set(True)

    def _sync_model_controls(self) -> None:
        enabled = self.model_mode_var.get().startswith("选择")
        state = NORMAL if enabled else DISABLED
        self.model_entry.configure(state=state)
        self.model_button.configure(state=state)

    def _validate(self) -> tuple[Path, Path, Path, Path | None] | None:
        source_text = self.input_var.get().strip()
        output_text = self.output_var.get().strip()
        source = Path(source_text)
        output = Path(output_text)
        if not source.is_file():
            messagebox.showerror(APP_NAME, "请选择存在的音频或视频文件。")
            return None
        if not output_text:
            messagebox.showerror(APP_NAME, "请选择输出目录。")
            return None

        if self.model_mode_var.get().startswith("内置"):
            model = bundled_path("models/ggml-small.bin")
        else:
            model = Path(self.custom_model_var.get().strip())
        if not model.is_file():
            messagebox.showerror(APP_NAME, f"模型文件不存在：\n{model}")
            return None

        glossary: Path | None = None
        if self.glossary_enabled_var.get():
            glossary = Path(self.glossary_var.get().strip())
            if not glossary.is_file():
                messagebox.showerror(APP_NAME, f"术语表不存在：\n{glossary}")
                return None

        try:
            invalid_numbers = (
                self.chunk_var.get() < 10
                or self.threads_var.get() < 1
                or self.beam_var.get() < 1
            )
        except Exception:
            invalid_numbers = True
        if invalid_numbers:
            messagebox.showerror(APP_NAME, "切片秒数至少为 10，线程数和 Beam 至少为 1。")
            return None
        return source, output, model, glossary

    def _start(self) -> None:
        validated = self._validate()
        if validated is None or self.is_running:
            return
        source, output, model, glossary = validated

        args = argparse.Namespace(
            input=source,
            model=str(model),
            models_dir=app_data_dir() / "models",
            model_base_url=transcribe_audio.DEFAULT_MODEL_BASE_URL,
            download_retries=10,
            download_timeout=90,
            language=None if self.language_var.get() == "auto" else self.language_var.get(),
            output_dir=output,
            chunk_seconds=self.chunk_var.get(),
            threads=self.threads_var.get(),
            beam_size=self.beam_var.get(),
            glossary=glossary,
            keep_temp=False,
            quiet=False,
        )
        self.is_running = True
        self.start_button.configure(state=DISABLED)
        self.progress.start(12)
        self._append_log("")
        self._append_log("=" * 68)
        self._append_log("转写任务已开始。长音频可能需要较长时间，请保持程序运行。")
        self.worker = threading.Thread(target=self._run_worker, args=(args,), daemon=True)
        self.worker.start()

    def _run_worker(self, args: argparse.Namespace) -> None:
        writer = QueueWriter(self.messages)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                files = transcribe_audio.transcribe(args)
            writer.flush()
            self.messages.put(("done", files))
        except BaseException as exc:
            writer.flush()
            if isinstance(exc, SystemExit):
                message = str(exc) or "转写程序异常退出。"
            else:
                message = f"{type(exc).__name__}: {exc}"
                self.messages.put(("log", traceback.format_exc()))
            self.messages.put(("error", message))

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "done":
                    self._finish()
                    files = payload
                    messagebox.showinfo(
                        APP_NAME,
                        f"转写完成，共生成 {len(files)} 个文件。\n\n输出目录：\n{self.output_var.get()}",
                    )
                elif kind == "error":
                    self._finish()
                    self._append_log(f"错误：{payload}")
                    messagebox.showerror(APP_NAME, f"转写失败：\n\n{payload}")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_messages)

    def _finish(self) -> None:
        self.is_running = False
        self.progress.stop()
        self.start_button.configure(state=NORMAL)

    def _append_log(self, text: str) -> None:
        self.log.configure(state=NORMAL)
        self.log.insert(END, text + "\n")
        self.log.see(END)
        self.log.configure(state=DISABLED)

    def _clear_log(self) -> None:
        self.log.configure(state=NORMAL)
        self.log.delete("1.0", END)
        self.log.configure(state=DISABLED)

    def _open_output(self) -> None:
        try:
            open_in_explorer(Path(self.output_var.get().strip()))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"无法打开输出目录：\n{exc}")

    def _on_close(self) -> None:
        if self.is_running and not messagebox.askyesno(
            APP_NAME,
            "转写仍在进行。现在退出会中断任务，且不会生成完整结果。\n\n确定退出吗？",
        ):
            return
        self.root.destroy()


def smoke_test() -> None:
    """Exercise frozen imports and bundled resources without opening the main window."""
    from pywhispercpp.model import Model

    ffmpeg = transcribe_audio.find_program("ffmpeg")
    model = bundled_path("models/ggml-small.bin")
    glossary = bundled_path("glossary.example.txt")
    if not ffmpeg:
        raise RuntimeError("Bundled ffmpeg not found")
    if not transcribe_audio.verify_model_file(model, "small", quiet=True):
        raise RuntimeError("Bundled small model is missing or invalid")
    if not glossary.is_file():
        raise RuntimeError("Bundled glossary not found")
    transcribe_audio.capture([ffmpeg, "-version"])
    whisper = Model(str(model), n_threads=1, language="zh", print_progress=False)
    del whisper
    root = Tk()
    root.withdraw()
    root.update()
    root.destroy()


def main() -> int:
    if "--smoke-test" in sys.argv:
        try:
            smoke_test()
            return 0
        except BaseException:
            error_file = Path(os.environ.get("TEMP", str(Path.cwd()))) / (
                "OfflineAudioTranscriber-smoke-test-error.txt"
            )
            error_file.write_text(traceback.format_exc(), encoding="utf-8")
            return 1

    root = Tk()
    TranscriberApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
