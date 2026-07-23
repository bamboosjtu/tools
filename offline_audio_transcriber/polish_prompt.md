# Transcript polishing prompt

Please edit the following machine transcript into a faithful Chinese transcript.

Rules:

1. Preserve facts, viewpoints, examples, numbers, and causal relationships. Do not add new content.
2. Remove meaningless filler words, false starts, and immediate repetitions, but retain meaningful conversational tone.
3. Correct obvious homophone errors using the supplied glossary and context.
4. Do not guess uncertain names, numbers, or technical terms. Mark them as `[听不清：可能为……]`.
5. Add punctuation and paragraph breaks. Each paragraph should contain one coherent idea.
6. Do not invent speaker identities. Use `说话人 A` and `说话人 B` only when the audio or context supports a speaker change.
7. Output a second section listing uncertain passages with timestamps for manual review.

Glossary:

- DCP Lite
- GIM Viewer
- OpenCode
- SDK / CLI / Skill
- Vibe Coding / Vibe Research / Agentic AI
- 数字孪生
- 输变电工程

Machine transcript:

```text
PASTE THE CONTENT OF *_raw_timestamps.txt HERE
```
