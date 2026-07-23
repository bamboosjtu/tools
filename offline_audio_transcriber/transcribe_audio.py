#!/usr/bin/env python3
"""Offline long-audio transcription with ffmpeg + pywhispercpp.

Produces:
- *_raw_timestamps.txt
- *_raw.srt
- *_raw.md (timestamp-free, grouped into readable paragraphs)
- *_segments.json

The script mirrors the workflow used for the user's recordings:
1. Convert to 16 kHz mono WAV.
2. Split long audio into fixed-length chunks.
3. Transcribe each chunk with a multilingual whisper.cpp model.
4. Add chunk offsets and merge the global timeline.
5. Export timestamped text, SRT, Markdown, and JSON.

A polished transcript still needs semantic review. ASR cannot reliably infer speaker
identity or correct domain terms without human/LLM checking.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_MODEL_BASE_URL = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
)

# SHA-1 values published by whisper.cpp for the main, non-English-only models.
# Models not listed here can still be downloaded, but only listed models receive
# an automatic content checksum verification.
MODEL_SHA1: dict[str, str] = {
    "tiny": "bd577a113a864445d4c299885e0cb97d4ba92b5f",
    "base": "465707469ff3a37a2b9b8d8f89f2f99de7299dac",
    "small": "55356645c2b361a969dfd0ef2c5a50d530afd8d5",
    "medium": "fd9727b6e1217c2f614f9b698455c4ffd82463b4",
    "large-v1": "b1caaf735c4cc1429223d5a74f0f4d0b9b59a299",
    "large-v2": "0f4c8e34f21cf1a914c59d8b3ce882345ad349d6",
    "large-v2-q5_0": "00e39f2196344e901b3a2bd5814807a769bd1630",
    "large-v3": "ad82bf6a9043ceed055076d0fd39f5f186ff8062",
    "large-v3-q5_0": "e6e2ed78495d403bef4b7cff42ef4aaadcfea8de",
    "large-v3-turbo": "4af2b29d7ec73d781377bfd1758ca957a807e941",
    "large-v3-turbo-q5_0": "e050f7970618a659205450ad97eb95a18d69c9ee",
}

@dataclass
class Segment:
    start: float
    end: float
    text: str
    confidence: float | None = None
    chunk_index: int | None = None


def resource_root() -> Path:
    """Return the source directory or PyInstaller's bundled resource directory."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parent


def find_program(program: str) -> str | None:
    """Find ffmpeg tools in the portable bundle before checking PATH."""
    executable_name = f"{program}.exe" if os.name == "nt" else program
    bundled = resource_root() / "bin" / executable_name
    if bundled.is_file():
        return str(bundled)
    bundled_bin = resource_root() / "bin"
    if bundled_bin.is_dir():
        matches = sorted(bundled_bin.glob(f"{program}*.exe"))
        if matches:
            return str(matches[0])
    return shutil.which(program)


def _subprocess_options() -> dict[str, int]:
    """Keep helper executables from flashing console windows in GUI builds."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run(command: list[str]) -> None:
    """Run a command and fail with a readable error."""
    try:
        subprocess.run(command, check=True, **_subprocess_options())
    except FileNotFoundError as exc:
        raise SystemExit(f"Required program not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed with exit code {exc.returncode}: {' '.join(command)}") from exc


def capture(command: list[str]) -> str:
    """Run a command and return stdout."""
    try:
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **_subprocess_options(),
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"Required program not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip()
        raise SystemExit(f"Command failed: {' '.join(command)}\n{message}") from exc
    return result.stdout.strip()


def require_ffmpeg() -> None:
    if find_program("ffmpeg") is None:
        raise SystemExit(
            "ffmpeg is unavailable. Reinstall the portable app or see README.md."
        )


def probe_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            frame_rate = handle.getframerate()
            if frame_rate <= 0:
                raise ValueError("invalid WAV frame rate")
            return handle.getnframes() / frame_rate
    except (OSError, EOFError, wave.Error, ValueError) as exc:
        raise SystemExit(f"Could not read WAV duration for {path}: {exc}") from exc


def convert_to_wav(source: Path, target: Path) -> None:
    run(
        [
            find_program("ffmpeg") or "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(target),
        ]
    )


def split_wav(source: Path, chunk_dir: Path, chunk_seconds: int) -> list[Path]:
    pattern = chunk_dir / "chunk_%04d.wav"
    run(
        [
            find_program("ffmpeg") or "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-reset_timestamps",
            "1",
            "-c",
            "copy",
            str(pattern),
        ]
    )
    chunks = sorted(chunk_dir.glob("chunk_*.wav"))
    if not chunks:
        raise SystemExit("ffmpeg did not create any chunks.")
    return chunks


def normalize_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([，。！？；：、,.!?;:])", r"\1", text)
    return text


def load_glossary(path: Path | None) -> str:
    if path is None:
        return ""
    if not path.exists():
        raise SystemExit(f"Glossary file does not exist: {path}")
    terms = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    terms = [term for term in terms if term and not term.startswith("#")]
    return "，".join(terms)


def sha1_file(path: Path, block_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def verify_model_file(path: Path, model_name: str, quiet: bool = False) -> bool:
    """Return True when a model file exists and passes the known checksum."""
    if not path.is_file() or path.stat().st_size < 1_000_000:
        return False

    expected = MODEL_SHA1.get(model_name)
    if expected is None:
        if not quiet:
            print(
                f"Model checksum is not built in for {model_name!r}; "
                "using the existing file without SHA-1 verification."
            )
        return True

    if not quiet:
        print(f"Verifying model SHA-1: {path}")
    actual = sha1_file(path)
    if actual.lower() == expected.lower():
        return True

    if not quiet:
        print(
            "Model checksum mismatch. "
            f"Expected {expected}, got {actual}. The file will be downloaded again."
        )
    return False


def _read_total_size(response, offset: int) -> int | None:
    content_range = response.headers.get("content-range", "")
    match = re.match(r"bytes\s+\d+-\d+/(\d+)", content_range, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))

    content_length = response.headers.get("content-length")
    if content_length and content_length.isdigit():
        remaining = int(content_length)
        return offset + remaining if response.status_code == 206 else remaining
    return None


def _print_download_progress(
    downloaded: int,
    total: int | None,
    model_name: str,
    final: bool = False,
) -> None:
    downloaded_mib = downloaded / (1024 * 1024)
    if total:
        total_mib = total / (1024 * 1024)
        percent = min(100.0, downloaded * 100.0 / total)
        message = (
            f"\rDownloading {model_name}: {downloaded_mib:.1f}/{total_mib:.1f} MiB "
            f"({percent:5.1f}%)"
        )
    else:
        message = f"\rDownloading {model_name}: {downloaded_mib:.1f} MiB"
    print(message, end="\n" if final else "", flush=True)


def download_model_resumable(
    model_name: str,
    target: Path,
    base_url: str,
    retries: int,
    timeout: int,
    quiet: bool,
) -> Path:
    """Download a whisper.cpp GGML model with resume, retries, and checksum."""
    try:
        import requests
    except ImportError as exc:
        raise SystemExit(
            "requests is not installed. Run: python -m pip install -r requirements.txt"
        ) from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    url = f"{base_url.rstrip('/')}/ggml-{model_name}.bin"
    headers_base = {"User-Agent": "offline-audio-transcriber/2.0"}
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = dict(headers_base)
        if offset:
            headers["Range"] = f"bytes={offset}-"

        if not quiet:
            action = "Resuming" if offset else "Downloading"
            print(
                f"{action} model {model_name} "
                f"(attempt {attempt}/{retries}, starting at {offset / (1024 * 1024):.1f} MiB)"
            )

        try:
            with requests.get(
                url,
                stream=True,
                allow_redirects=True,
                headers=headers,
                timeout=(20, timeout),
            ) as response:
                # A completed partial file may receive 416 when requesting its end.
                if response.status_code == 416 and partial.exists():
                    if verify_model_file(partial, model_name, quiet):
                        os.replace(partial, target)
                        return target.resolve()
                    partial.unlink(missing_ok=True)
                    raise RuntimeError("Server rejected the range and the partial file is invalid")

                response.raise_for_status()

                resumed = offset > 0 and response.status_code == 206
                if offset > 0 and not resumed:
                    # Server ignored Range; restart cleanly instead of appending duplicates.
                    offset = 0
                    mode = "wb"
                else:
                    mode = "ab" if resumed else "wb"

                total = _read_total_size(response, offset)
                downloaded = offset
                last_progress = 0.0

                with partial.open(mode) as handle:
                    for data in response.iter_content(chunk_size=1024 * 1024):
                        if not data:
                            continue
                        handle.write(data)
                        downloaded += len(data)
                        now = time.monotonic()
                        if not quiet and now - last_progress >= 0.75:
                            _print_download_progress(downloaded, total, model_name)
                            last_progress = now
                    handle.flush()
                    os.fsync(handle.fileno())

                if not quiet:
                    _print_download_progress(downloaded, total, model_name, final=True)

                if total is not None and downloaded < total:
                    raise IOError(
                        f"Download ended early at {downloaded} bytes; expected {total} bytes"
                    )

            if not verify_model_file(partial, model_name, quiet):
                partial.unlink(missing_ok=True)
                raise IOError("Downloaded model failed size or SHA-1 verification")

            os.replace(partial, target)
            if not quiet:
                print(f"Model saved to: {target.resolve()}")
            return target.resolve()

        except (requests.RequestException, OSError, RuntimeError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            delay = min(30, 2 ** min(attempt - 1, 5))
            if not quiet:
                retained = partial.stat().st_size if partial.exists() else 0
                print(
                    f"Download interrupted: {exc}\n"
                    f"Kept {retained / (1024 * 1024):.1f} MiB; retrying in {delay}s..."
                )
            time.sleep(delay)

    raise SystemExit(
        f"Model download failed after {retries} attempts: {last_error}\n"
        f"Partial data was kept at: {partial.resolve()}\n"
        "Run the same command again to resume, or manually download the model "
        "and pass its local .bin path with --model."
    )


def resolve_model(args: argparse.Namespace) -> Path:
    """Resolve a local model path or download a named model robustly."""
    raw = os.path.expandvars(os.path.expanduser(str(args.model)))
    candidate = Path(raw)

    if candidate.is_file():
        return candidate.resolve()

    path_like = (
        candidate.suffix.lower() == ".bin"
        or "/" in raw
        or "\\" in raw
        or raw.startswith(".")
    )
    if path_like:
        raise SystemExit(f"Local model file does not exist: {candidate.resolve()}")

    if not re.fullmatch(r"[A-Za-z0-9._-]+", raw):
        raise SystemExit(f"Invalid model name: {raw!r}")

    model_name = raw
    models_dir = args.models_dir.expanduser().resolve()
    target = models_dir / f"ggml-{model_name}.bin"

    if verify_model_file(target, model_name, args.quiet):
        if not args.quiet:
            print(f"Using cached model: {target}")
        return target

    if target.exists():
        corrupt = target.with_name(target.name + ".corrupt")
        corrupt.unlink(missing_ok=True)
        target.replace(corrupt)
        if not args.quiet:
            print(f"Moved invalid model to: {corrupt}")

    return download_model_resumable(
        model_name=model_name,
        target=target,
        base_url=args.model_base_url,
        retries=args.download_retries,
        timeout=args.download_timeout,
        quiet=args.quiet,
    )


def build_model(args: argparse.Namespace, model_path: Path):
    try:
        from pywhispercpp.model import Model
    except ImportError as exc:
        raise SystemExit(
            "pywhispercpp is not installed. Run: pip install -r requirements.txt"
        ) from exc
    model_kwargs = {
        "n_threads": args.threads,
        "print_progress": not args.quiet,
        "print_realtime": False,
        "no_context": True,
        "temperature": 0.0,
    }
    if args.language:
        model_kwargs["language"] = args.language
    if args.beam_size > 1:
        model_kwargs["params_sampling_strategy"] = 1
        model_kwargs["beam_search"] = {
            "beam_size": args.beam_size,
            "patience": 1.0,
        }
    return Model(str(model_path), **model_kwargs)


def transcribe_chunks(
    model,
    chunks: Iterable[Path],
    glossary_prompt: str,
    quiet: bool,
) -> list[Segment]:
    merged: list[Segment] = []
    offset = 0.0

    chunks = list(chunks)
    for index, chunk in enumerate(chunks):
        duration = probe_duration(chunk)
        if not quiet:
            print(
                f"[{index + 1}/{len(chunks)}] Transcribing {chunk.name} "
                f"({duration:.1f}s, global offset {offset:.1f}s)",
                flush=True,
            )

        transcribe_kwargs = {"extract_probability": True}
        if glossary_prompt:
            transcribe_kwargs["initial_prompt"] = glossary_prompt

        try:
            result = model.transcribe(str(chunk), **transcribe_kwargs)
        except Exception as exc:  # pywhispercpp exposes backend errors as generic exceptions
            raise SystemExit(f"Transcription failed for {chunk}: {exc}") from exc

        for item in result:
            text = normalize_text(item.text)
            if not text:
                continue
            probability = getattr(item, "probability", None)
            merged.append(
                Segment(
                    start=offset + item.t0 / 100.0,
                    end=offset + item.t1 / 100.0,
                    text=text,
                    confidence=float(probability) if probability is not None else None,
                    chunk_index=index,
                )
            )
        offset += duration

    return merged


def timestamp(seconds: float, srt: bool = False) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    separator = "," if srt else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def safe_stem(path: Path) -> str:
    stem = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "_", path.stem, flags=re.UNICODE)
    return stem.strip("_") or "transcript"


def _needs_space(left: str, right: str) -> bool:
    """Return True when joining two Latin/number tokens needs a space."""
    return bool(
        left
        and right
        and re.match(r"[A-Za-z0-9]", left[-1])
        and re.match(r"[A-Za-z0-9]", right[0])
    )


def _join_paragraph_parts(parts: list[str]) -> str:
    paragraph = ""
    for part in parts:
        text = normalize_text(part)
        if not text:
            continue
        if paragraph and _needs_space(paragraph, text):
            paragraph += " "
        paragraph += text
    return paragraph


def _split_long_segment(text: str, max_chars: int) -> list[str]:
    """Split an unusually long ASR segment at the best nearby text boundary."""
    pieces: list[str] = []
    remaining = text
    minimum_boundary = max(1, int(max_chars * 0.55))

    while len(remaining) > max_chars:
        window = remaining[: max_chars + 1]
        cut = -1
        for pattern in (
            r"[。！？!?；;…][”’\"]?",
            r"[，,：:]",
            r"\s+",
        ):
            matches = list(re.finditer(pattern, window))
            viable = [match.end() for match in matches if match.end() >= minimum_boundary]
            if viable:
                cut = viable[-1]
                break
        if cut < 1:
            cut = max_chars
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()

    if remaining:
        pieces.append(remaining)
    return pieces


def segments_to_paragraphs(
    segments: list[Segment],
    target_chars: int = 180,
    max_chars: int = 300,
    pause_seconds: float = 1.8,
) -> list[str]:
    """Group ASR segments into readable paragraphs without timestamps.

    Paragraph boundaries prefer a meaningful pause or sentence-ending
    punctuation. The hard length limit prevents punctuation-poor ASR output
    from becoming a single wall of text.
    """
    paragraphs: list[str] = []
    current: list[str] = []
    current_length = 0
    previous_end: float | None = None

    def flush() -> None:
        nonlocal current, current_length
        paragraph = _join_paragraph_parts(current)
        if paragraph:
            paragraphs.append(paragraph)
        current = []
        current_length = 0

    for segment in segments:
        text = normalize_text(segment.text)
        if not text:
            continue

        gap = 0.0 if previous_end is None else max(0.0, segment.start - previous_end)
        if current and gap >= pause_seconds:
            flush()

        pieces = _split_long_segment(text, max_chars)
        for index, piece in enumerate(pieces):
            if current and current_length + len(piece) > max_chars:
                carry: list[str] = []
                while (
                    len(current) > 1
                    and len(current[-1]) <= 12
                    and current_length - len(current[-1]) >= target_chars
                ):
                    trailing = current.pop()
                    current_length -= len(trailing)
                    carry.insert(0, trailing)
                flush()
                current.extend(carry)
                current_length = sum(len(item) for item in current)
            current.append(piece)
            current_length += len(piece)
            if index < len(pieces) - 1:
                flush()
        previous_end = segment.end

        ends_sentence = bool(re.search(r"[。！？!?；;…][”’\"]?$", text))
        if current_length >= max_chars or (
            current_length >= target_chars and ends_sentence
        ):
            flush()

    flush()
    return paragraphs


def write_outputs(segments: list[Segment], output_dir: Path, stem: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp_file = output_dir / f"{stem}_raw_timestamps.txt"
    srt_file = output_dir / f"{stem}_raw.srt"
    markdown_file = output_dir / f"{stem}_raw.md"
    json_file = output_dir / f"{stem}_segments.json"

    with timestamp_file.open("w", encoding="utf-8") as handle:
        for segment in segments:
            confidence = ""
            if segment.confidence is not None:
                confidence = f" [confidence={segment.confidence:.2f}]"
            handle.write(
                f"[{timestamp(segment.start)} --> {timestamp(segment.end)}] "
                f"{segment.text}{confidence}\n"
            )

    with srt_file.open("w", encoding="utf-8") as handle:
        for index, segment in enumerate(segments, start=1):
            handle.write(f"{index}\n")
            handle.write(
                f"{timestamp(segment.start, srt=True)} --> "
                f"{timestamp(segment.end, srt=True)}\n"
            )
            handle.write(f"{segment.text}\n\n")

    with markdown_file.open("w", encoding="utf-8") as handle:
        handle.write(f"# {stem} 机器转写稿\n\n")
        handle.write(
            "> 该文件由离线 Whisper 自动生成。人名、行业术语、数字和说话人需要复核。\n\n"
        )
        for paragraph in segments_to_paragraphs(segments):
            handle.write(f"{paragraph}\n\n")

    json_file.write_text(
        json.dumps([asdict(segment) for segment in segments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return [timestamp_file, srt_file, markdown_file, json_file]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline long-audio transcription with ffmpeg and pywhispercpp."
    )
    parser.add_argument("input", type=Path, help="Input audio/video file, e.g. recording.m4a")
    parser.add_argument(
        "--model",
        default="tiny",
        help=(
            "whisper.cpp model name (tiny/base/small/medium/large-v3-turbo) "
            "or a local ggml model path. Named models are downloaded with resume support. "
            "Default: tiny"
        ),
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path("models"),
        help="Directory for downloaded models. Default: ./models",
    )
    parser.add_argument(
        "--model-base-url",
        default=os.environ.get("WHISPER_MODEL_BASE_URL", DEFAULT_MODEL_BASE_URL),
        help=(
            "Base URL containing ggml-<model>.bin files. Can also be set with "
            "WHISPER_MODEL_BASE_URL."
        ),
    )
    parser.add_argument(
        "--download-retries",
        type=int,
        default=10,
        help="Maximum model download attempts; partial data is resumed. Default: 10",
    )
    parser.add_argument(
        "--download-timeout",
        type=int,
        default=90,
        help="Model download read timeout in seconds. Default: 90",
    )
    parser.add_argument("--language", default="zh", help="Language code. Default: zh")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("transcript_output"), help="Output directory"
    )
    parser.add_argument(
        "--chunk-seconds",
        type=int,
        default=60,
        help="Chunk length in seconds. Default: 60",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=max(1, min(8, os.cpu_count() or 4)),
        help="CPU threads used by whisper.cpp",
    )
    parser.add_argument(
        "--beam-size", type=int, default=5, help="Beam search size; set 1 for greedy decoding"
    )
    parser.add_argument(
        "--glossary",
        type=Path,
        help="UTF-8 text file containing one domain term per line",
    )
    parser.add_argument("--keep-temp", action="store_true", help="Keep normalized WAV and chunks")
    parser.add_argument("--quiet", action="store_true", help="Reduce console output")
    args = parser.parse_args()

    if args.chunk_seconds < 10:
        parser.error("--chunk-seconds must be at least 10")
    if args.threads < 1:
        parser.error("--threads must be at least 1")
    if args.beam_size < 1:
        parser.error("--beam-size must be at least 1")
    if args.download_retries < 1:
        parser.error("--download-retries must be at least 1")
    if args.download_timeout < 10:
        parser.error("--download-timeout must be at least 10")
    return args


def transcribe(args: argparse.Namespace) -> list[Path]:
    """Run the transcription pipeline for CLI and GUI callers."""
    source = args.input.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise SystemExit(f"Input file does not exist: {source}")

    require_ffmpeg()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(source)
    glossary_prompt = load_glossary(args.glossary)

    if not args.quiet:
        print(f"Input: {source}")
        print(f"Model: {args.model}")
        print(f"Output: {output_dir}")
        if glossary_prompt:
            print(f"Glossary prompt: {glossary_prompt}")
        print("Preparing Whisper model...")

    model_path = resolve_model(args)

    temporary_parent = output_dir if args.keep_temp else None
    with tempfile.TemporaryDirectory(prefix=f"{stem}_work_", dir=temporary_parent) as temp_name:
        work_dir = Path(temp_name)
        normalized_wav = work_dir / f"{stem}_16k_mono.wav"
        chunk_dir = work_dir / "chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)

        if not args.quiet:
            print("Converting to 16 kHz mono WAV...")
        convert_to_wav(source, normalized_wav)

        if not args.quiet:
            print(f"Splitting into {args.chunk_seconds}-second chunks...")
        chunks = split_wav(normalized_wav, chunk_dir, args.chunk_seconds)

        if args.keep_temp:
            # TemporaryDirectory deletes itself. Copy files to a persistent debug directory first.
            debug_dir = output_dir / f"{stem}_debug_audio"
            if debug_dir.exists():
                shutil.rmtree(debug_dir)
            debug_dir.mkdir(parents=True)
            shutil.copy2(normalized_wav, debug_dir / normalized_wav.name)
            shutil.copytree(chunk_dir, debug_dir / "chunks")

        if not args.quiet:
            print("Loading Whisper model...")
        model = build_model(args, model_path)
        segments = transcribe_chunks(model, chunks, glossary_prompt, args.quiet)

    if not segments:
        raise SystemExit("No speech segments were produced.")

    files = write_outputs(segments, output_dir, stem)
    if not args.quiet:
        print("\nCreated:")
        for path in files:
            print(f"- {path}")
        print(
            "\nNote: speaker labels and polished prose require a separate semantic review step."
        )
    return files


def main() -> int:
    args = parse_args()
    transcribe(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
