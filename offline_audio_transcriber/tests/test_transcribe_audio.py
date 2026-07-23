from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from transcribe_audio import (
    Segment,
    load_glossary,
    segments_to_paragraphs,
    transcribe_chunks,
    write_outputs,
)


class FakeWhisperSegment:
    t0 = 0
    t1 = 50
    text = "GIM"
    probability = 0.9


class CapturingModel:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def transcribe(self, _path: str, **kwargs):
        self.calls.append(kwargs)
        return [FakeWhisperSegment()]


def make_wav(path: Path, seconds: float = 0.1) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * int(16000 * seconds))


class TranscribeAudioTests(unittest.TestCase):
    def test_markdown_has_natural_paragraphs_without_timestamps(self) -> None:
        segments = [
            Segment(0.0, 1.0, "第一段开头"),
            Segment(1.0, 2.0, "继续说明。"),
            Segment(4.5, 5.5, "第二段内容"),
            Segment(5.5, 6.5, "自然结束。"),
        ]
        paragraphs = segments_to_paragraphs(
            segments, target_chars=20, max_chars=100, pause_seconds=1.8
        )
        self.assertEqual(paragraphs, ["第一段开头继续说明。", "第二段内容自然结束。"])

        with tempfile.TemporaryDirectory() as temp_name:
            output = Path(temp_name)
            files = write_outputs(segments, output, "sample")
            markdown = next(path for path in files if path.suffix == ".md")
            content = markdown.read_text(encoding="utf-8")
            self.assertNotIn("00:00:", content)
            self.assertNotIn("**", content)
            self.assertIn("第一段开头继续说明。\n\n第二段内容自然结束。", content)

    def test_single_long_segment_is_split_near_text_boundaries(self) -> None:
        long_text = (
            "这是第一部分，需要保留自然语义，继续补充一些内容。"
            "这是第二部分，也应该在合适的位置进行分段，避免形成一整面文字墙。"
            "最后是第三部分，用于验证单个超长识别片段也能被拆开。"
        )
        paragraphs = segments_to_paragraphs(
            [Segment(0.0, 10.0, long_text)],
            target_chars=35,
            max_chars=45,
        )
        self.assertGreater(len(paragraphs), 1)
        self.assertTrue(all(len(paragraph) <= 45 for paragraph in paragraphs))
        self.assertEqual("".join(paragraphs), long_text)

    def test_glossary_is_passed_to_every_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            glossary = temp_dir / "glossary.txt"
            glossary.write_text("# comment\nGIM\n\n数字孪生\n", encoding="utf-8")
            prompt = load_glossary(glossary)
            self.assertEqual(prompt, "GIM，数字孪生")

            chunks = [temp_dir / "chunk_0000.wav", temp_dir / "chunk_0001.wav"]
            for chunk in chunks:
                make_wav(chunk)

            model = CapturingModel()
            transcribe_chunks(model, chunks, prompt, quiet=True)
            self.assertEqual(len(model.calls), 2)
            for call in model.calls:
                self.assertEqual(call["initial_prompt"], "GIM，数字孪生")
                self.assertTrue(call["extract_probability"])


if __name__ == "__main__":
    unittest.main()
