#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF 平铺水印移除工具
====================
用途：移除 PDF 中后期叠加的平铺式文字水印（如斜排灰色公司名/人名/日期水印）。

原理：
  策略一（首选）：水印通常由 iText/WPS 等工具追加为每页独立的"内容流"，
    特征为——旋转文字矩阵 + 灰色等值 RGB 填充 + 平铺重复文字。
    直接将该流替换为平衡图形状态的 " Q"，正文零损伤。
  策略二（兜底）：若水印混在正文流中，则按指定水印文字做精确匹配删字（redaction），
    仅删除与水印文字完全一致的片段，避免误伤正文。

用法：
  remove_watermark 输入.pdf                       # 输出为 输入（无水印）.pdf
  remove_watermark 输入.pdf -o 输出.pdf           # 指定输出路径
  remove_watermark 输入.pdf -t "水印文字"          # 指定水印文字（启用兜底策略）
  remove_watermark 某个目录                        # 批量处理目录下所有 PDF
  也可直接把 PDF 文件拖到 exe 图标上。

依赖：pymupdf
"""

import argparse
import os
import re
import sys

try:
    import pymupdf
except ImportError:
    print("错误：缺少 pymupdf，请先执行: pip install pymupdf")
    sys.exit(1)


# ---------------------------------------------------------------- 策略一：内容流级移除

# 旋转文字矩阵 Tm：a b -b a e f（a=d=cosθ, b=-c=sinθ, |b| 明显非零）
_TM_ROT = re.compile(
    rb"([01](?:\.\d+)?)\s+([+-]?(?:0?\.\d+))\s+-\2\s+\1\s+[+-]?[\d.]+\s+[+-]?[\d.]+\s+Tm"
)
# 灰色等值 RGB 填充，如 "0.6 0.6 0.6 rg"
_GRAY_RG = re.compile(
    rb"([0-9.]+)\s+\1\s+\1\s+rg"
)


def _is_watermark_stream(data: bytes) -> bool:
    """判断一个内容流是否为叠加的平铺水印流（启发式特征）。"""
    if not data or len(data) > 300_000:          # 水印流一般只有几 KB
        return False
    stripped = data.lstrip()
    if not (stripped.startswith(b"Q") or stripped.startswith(b"q")):
        return False
    if b"BT" not in data or (b"Tj" not in data and b"TJ" not in data):
        return False                              # 必须是绘制文字的流
    if not b"Tm" in data:
        return False
    rotated = any(
        abs(float(m.group(2))) > 0.03 and m.group(1) != b"1"
        for m in _TM_ROT.finditer(data)
    )
    if not rotated:                               # 必须含旋转文字（平铺水印典型特征）
        return False
    if not _GRAY_RG.search(data):                 # 必须是灰色（等值 RGB）
        return False
    # 旋转+灰色的绘制流在正文中极罕见；再要求整流以 BT/ET 文字块为主
    return data.count(b"BT") >= 1


def _remove_stream_watermarks(doc) -> int:
    """删除各页独立的水印内容流，返回删除的流数。"""
    removed = 0
    for page in doc:
        for xref in page.get_contents():
            if not xref or not doc.xref_is_stream(xref):
                continue
            data = doc.xref_stream(xref)
            if data and _is_watermark_stream(data):
                # 原流以 " Q" 开头，用于闭合正文流开头的 "q"，需保留以维持图形状态平衡
                replacement = b" Q\n" if data.lstrip().startswith(b"Q") else b"\n"
                doc.update_stream(xref, replacement)
                removed += 1
    return removed


# ---------------------------------------------------------------- 策略二：文字级兜底

def _remove_text_watermark(doc, text: str) -> tuple[int, int]:
    """按水印文字做精确 redaction，返回 (处理页数, 因保护正文而跳过的实例数)。

    安全规则：斜排水印必须用 quads=True 取精确四边形；且若某水印实例的
    覆盖区域与正文文字段明显重叠，则跳过该实例——宁可漏删，不可误删正文。
    """
    pages_done = skipped = 0
    for page in doc:
        quads = page.search_for(text, quads=True)
        if not quads:
            continue
        # 收集"非水印"正文文字段的矩形
        body_rects = []
        for blk in page.get_text("dict")["blocks"]:
            if blk.get("type") != 0:
                continue
            for line in blk["lines"]:
                for span in line["spans"]:
                    if text not in span["text"]:
                        body_rects.append(pymupdf.Rect(span["bbox"]))
        safe = []
        for q in quads:
            qr = q.rect
            harmful = any(
                not (qr & br).is_empty and (qr & br).get_area() > 0.3 * br.get_area()
                for br in body_rects
            )
            if harmful:
                skipped += 1
            else:
                safe.append(q)
        if safe:
            for q in safe:
                page.add_redact_annot(q)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
            pages_done += 1
    return pages_done, skipped


# ---------------------------------------------------------------- 主流程

def default_output(path: str) -> str:
    root, ext = os.path.splitext(path)
    return root + "（无水印）" + (ext or ".pdf")


def process_pdf(src: str, dst: str | None = None, wm_text: str | None = None) -> tuple[bool, str]:
    """处理单个 PDF，返回 (是否成功, 说明)。"""
    dst = dst or default_output(src)
    try:
        doc = pymupdf.open(src)
    except Exception as e:
        return False, f"无法打开：{e}"

    if doc.needs_pass:
        doc.close()
        return False, "文件已加密，跳过"

    n_streams = _remove_stream_watermarks(doc)

    if wm_text:
        n_pages, n_skip = _remove_text_watermark(doc, wm_text)
    else:
        n_pages = n_skip = 0

    if n_streams == 0 and (not wm_text or n_pages == 0):
        doc.close()
        hint = "（可尝试用 -t 指定水印文字）" if not wm_text else f"（{n_skip} 处因与正文重叠被保护跳过）"
        return False, "未检测到可移除的水印" + hint

    try:
        doc.save(dst, garbage=4, deflate=True)
    except Exception as e:
        doc.close()
        return False, f"保存失败：{e}"
    doc.close()
    msg = f"移除水印流 {n_streams} 个" + (f"，文字清理 {n_pages} 页" if n_pages else "")
    if n_skip:
        msg += f"，保护性跳过 {n_skip} 处（与正文重叠）"
    return True, msg


def collect_batch_pdfs(directory: str) -> list[str]:
    """列出目录下待处理的 PDF（排除已生成的「（无水印）」文件）。"""
    return sorted(
        os.path.join(directory, f) for f in os.listdir(directory)
        if f.lower().endswith(".pdf") and "（无水印）" not in f
    )


def process_batch(
    target: str,
    out_dir: str | None = None,
    wm_text: str | None = None,
    log=print,
) -> tuple[int, int]:
    """批量处理目录下所有 PDF，返回 (成功数, 失败/跳过数)。"""
    pdfs = collect_batch_pdfs(target)
    if not pdfs:
        log(f"目录中没有 PDF 文件：{target}")
        return 0, 0
    out_dir = out_dir or target
    os.makedirs(out_dir, exist_ok=True)
    log(f"批量处理 {len(pdfs)} 个 PDF → {out_dir}\n" + "-" * 56)
    ok = fail = 0
    for src in pdfs:
        dst = os.path.join(out_dir, os.path.basename(default_output(src)))
        good, msg = process_pdf(src, dst, wm_text)
        log(f"{'✔' if good else '✘'} {os.path.basename(src)}  →  {msg}")
        ok, fail = ok + good, fail + (not good)
    log("-" * 56)
    log(f"完成：成功 {ok}，失败/跳过 {fail}")
    return ok, fail


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="PDF 平铺水印移除工具（支持整流移除与文字精确删除）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="输入 PDF 文件或目录")
    parser.add_argument("-o", "--output", help="输出路径（批量模式下为输出目录）")
    parser.add_argument("-t", "--text", help="水印文字（兜底策略：精确删字）")
    args = parser.parse_args()

    target = args.input.strip('"')

    # ---------- 批量模式 ----------
    if os.path.isdir(target):
        process_batch(target, args.output, args.text)
        return

    # ---------- 单文件模式 ----------
    if not os.path.isfile(target):
        print(f"输入不存在：{target}")
        sys.exit(1)
    good, msg = process_pdf(target, args.output, args.text)
    if good:
        print(f"✔ 处理完成：{args.output or default_output(target)}（{msg}）")
    else:
        print(f"✘ 处理失败：{msg}")
        sys.exit(1)


if __name__ == "__main__":
    # exe 双击运行且无参数时，停一下窗口方便看提示
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        print(__doc__)
        input("\n按回车键退出。也可直接把 PDF 拖到本 exe 图标上。")
    else:
        main()
