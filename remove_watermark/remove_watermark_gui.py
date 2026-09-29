#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 平铺水印移除工具 - 图形界面版。"""

from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
from pathlib import Path

# 无控制台的打包版里 stdout/stderr 可能为 None，先垫上空实现
for _stream in ("stdout", "stderr"):
    if getattr(sys, _stream, None) is None:
        _null = open(os.devnull, "w", encoding="utf-8", errors="replace")
        setattr(sys, _stream, _null)

from tkinter import (  # noqa: E402
    BOTH,
    DISABLED,
    END,
    LEFT,
    NORMAL,
    RIGHT,
    X,
    StringVar,
    Tk,
    filedialog,
    messagebox,
    ttk,
)
from tkinter.scrolledtext import ScrolledText  # noqa: E402

import remove_watermark  # noqa: E402

APP_NAME = "PDF 水印移除工具"
PDF_TYPES = [("PDF 文件", "*.pdf"), ("所有文件", "*.*")]


def open_in_explorer(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        import subprocess

        subprocess.Popen(["xdg-open", str(path)])


class WatermarkApp:
    def __init__(self, root: Tk, targets: list[str]) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("780x620")
        self.root.minsize(680, 500)
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))

        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.is_running = False
        self.targets: list[str] = []

        self.text_var = StringVar()
        self.outdir_var = StringVar()

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_messages)

        for target in targets:
            self._add_target(target)
        if self.targets:
            self.root.after(200, self._start)

    # ---------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill=BOTH, expand=True)

        ttk.Label(
            outer, text=APP_NAME, font=("Microsoft YaHei UI", 18, "bold")
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="本地处理 · 文件不上传 · 输出「xxx（无水印）.pdf」",
            foreground="#555555",
        ).pack(anchor="w", pady=(2, 14))

        files = ttk.LabelFrame(outer, text="待处理 PDF（可多选，也可选整个文件夹）", padding=10)
        files.pack(fill=BOTH, expand=True)
        self.listbox = ttk.Treeview(files, height=5, show="tree")
        self.listbox.pack(fill=BOTH, expand=True)
        bar = ttk.Frame(files)
        bar.pack(fill=X, pady=(8, 0))
        ttk.Button(bar, text="添加 PDF…", command=self._add_files).pack(side=LEFT)
        ttk.Button(bar, text="添加文件夹…", command=self._add_folder).pack(
            side=LEFT, padx=(8, 0)
        )
        ttk.Button(bar, text="移除选中", command=self._remove_selected).pack(
            side=LEFT, padx=(8, 0)
        )
        ttk.Button(bar, text="清空列表", command=self._clear_list).pack(side=RIGHT)

        options = ttk.LabelFrame(outer, text="选项", padding=10)
        options.pack(fill=X, pady=(12, 0))
        options.columnconfigure(1, weight=1)

        ttk.Label(options, text="水印文字").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=5
        )
        ttk.Entry(options, textvariable=self.text_var).grid(
            row=0, column=1, sticky="ew", pady=5
        )
        ttk.Label(
            options,
            text="留空 = 只用无损整流移除（推荐）；填入水印文字可启用精确删字兜底",
            foreground="#777777",
        ).grid(row=1, column=0, columnspan=2, sticky="w")

        ttk.Label(options, text="输出目录").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=5
        )
        out_row = ttk.Frame(options)
        out_row.grid(row=2, column=1, sticky="ew", pady=5)
        out_row.columnconfigure(0, weight=1)
        ttk.Entry(out_row, textvariable=self.outdir_var).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(out_row, text="浏览…", command=self._choose_output).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Label(
            options,
            text="留空 = 输出到原文件所在目录",
            foreground="#777777",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill=X, pady=12)
        self.start_button = ttk.Button(
            actions, text="开始处理", command=self._start, style="Accent.TButton"
        )
        self.start_button.pack(side=LEFT)
        ttk.Button(actions, text="打开输出位置", command=self._open_output).pack(
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
            height=10,
            state=DISABLED,
            font=("Consolas", 9),
        )
        self.log.pack(fill=BOTH, expand=True)
        self._append_log("添加 PDF 文件或文件夹，然后点击“开始处理”。")

    # ------------------------------------------------------------- 列表操作

    def _add_target(self, path: str) -> bool:
        path = path.strip().strip('"')
        if not path or not os.path.exists(path):
            return False
        if path in self.targets:
            return True
        self.targets.append(path)
        self.listbox.insert("", END, text=path)
        return True

    def _add_files(self) -> None:
        for path in filedialog.askopenfilenames(title="选择 PDF 文件", filetypes=PDF_TYPES):
            self._add_target(path)

    def _add_folder(self) -> None:
        path = filedialog.askdirectory(title="选择文件夹（批量处理其中所有 PDF）")
        if path:
            self._add_target(path)

    def _remove_selected(self) -> None:
        for item in self.listbox.selection():
            text = self.listbox.item(item, "text")
            self.listbox.delete(item)
            if text in self.targets:
                self.targets.remove(text)

    def _clear_list(self) -> None:
        self.listbox.delete(*self.listbox.get_children())
        self.targets.clear()

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录", mustexist=False)
        if path:
            self.outdir_var.set(path)

    # ------------------------------------------------------------- 处理

    def _start(self) -> None:
        if self.is_running:
            return
        if not self.targets:
            messagebox.showinfo(APP_NAME, "请先添加 PDF 文件或文件夹。")
            return
        text = self.text_var.get().strip() or None
        out_dir = self.outdir_var.get().strip() or None
        if out_dir:
            try:
                Path(out_dir).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror(APP_NAME, f"无法创建输出目录：\n{exc}")
                return

        self.is_running = True
        self.start_button.configure(state=DISABLED)
        self.progress.start(12)
        self._append_log("")
        self._append_log("=" * 60)
        self._append_log("处理开始。")
        threading.Thread(
            target=self._run_worker,
            args=(list(self.targets), out_dir, text),
            daemon=True,
        ).start()

    def _run_worker(self, targets: list[str], out_dir: str | None, text: str | None) -> None:
        def log(line: str) -> None:
            self.messages.put(("log", line))

        try:
            ok = fail = 0
            for target in targets:
                if os.path.isdir(target):
                    good, bad = remove_watermark.process_batch(
                        target, out_dir, text, log=log
                    )
                    ok, fail = ok + good, fail + bad
                else:
                    dst = None
                    if out_dir:
                        dst = os.path.join(
                            out_dir,
                            os.path.basename(remove_watermark.default_output(target)),
                        )
                    good, msg = remove_watermark.process_pdf(target, dst, text)
                    mark = "✔" if good else "✘"
                    log(f"{mark} {os.path.basename(target)}  →  {msg}")
                    ok, fail = ok + good, fail + (not good)
            self.messages.put(("done", (ok, fail)))
        except BaseException as exc:
            self.messages.put(("log", traceback.format_exc()))
            self.messages.put(("error", f"{type(exc).__name__}: {exc}"))

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "done":
                    ok, fail = payload  # type: ignore[misc]
                    self._finish()
                    self._append_log(f"全部完成：成功 {ok}，失败/跳过 {fail}")
                    messagebox.showinfo(
                        APP_NAME,
                        f"处理完成：成功 {ok} 个，失败/跳过 {fail} 个。\n\n"
                        f"输出位置：\n{self._output_root()}",
                    )
                elif kind == "error":
                    self._finish()
                    self._append_log(f"错误：{payload}")
                    messagebox.showerror(APP_NAME, f"处理失败：\n\n{payload}")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_messages)

    def _finish(self) -> None:
        self.is_running = False
        self.progress.stop()
        self.start_button.configure(state=NORMAL)

    # ------------------------------------------------------------- 杂项

    def _output_root(self) -> str:
        out_dir = self.outdir_var.get().strip()
        if out_dir:
            return out_dir
        if self.targets:
            first = self.targets[0]
            return first if os.path.isdir(first) else os.path.dirname(first)
        return ""

    def _open_output(self) -> None:
        path = self._output_root()
        if not path or not os.path.exists(path):
            messagebox.showerror(APP_NAME, "输出位置不存在。")
            return
        try:
            open_in_explorer(Path(path))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"无法打开输出位置：\n{exc}")

    def _append_log(self, text: str) -> None:
        self.log.configure(state=NORMAL)
        self.log.insert(END, text + "\n")
        self.log.see(END)
        self.log.configure(state=DISABLED)

    def _clear_log(self) -> None:
        self.log.configure(state=NORMAL)
        self.log.delete("1.0", END)
        self.log.configure(state=DISABLED)

    def _on_close(self) -> None:
        if self.is_running and not messagebox.askyesno(
            APP_NAME, "处理仍在进行，现在退出会中断任务。\n\n确定退出吗？"
        ):
            return
        self.root.destroy()


def smoke_test() -> None:
    """验证打包版里 pymupdf 与 Tk 都可用，不打开主窗口。"""
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    doc.close()
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
                "RemoveWatermark-smoke-test-error.txt"
            )
            error_file.write_text(traceback.format_exc(), encoding="utf-8")
            return 1

    targets = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Tk()
    WatermarkApp(root, targets)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
