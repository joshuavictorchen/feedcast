"""Filter a raw Claude Code session JSONL to user input + assistant text only.

Strips tool calls, tool results, progress events, system messages, and
file-history snapshots. Keeps only:
- User messages with string content (the human's actual input)
- Assistant messages that contain text blocks (final responses)

Tool-result records (user messages with list content) are dropped because
they contain system-injected tool output, not user-authored text.

Written for Claude Code CLI v2.1.83 session logs as of 2026-03-25
(Claude Opus 4.6). The JSONL format may change in future CLI versions.
Codex logs use a different format and are NOT supported by this script.

Usage:
    python plans/filter_claude_transcript.py <input.jsonl> [output.jsonl]

If output path is omitted, writes to YYMMDD-HHMMSS-<input-stem>-filtered.jsonl
in the same directory as the input file. The timestamp is the local time
when the script is run.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def filter_transcript(input_path: Path, output_path: Path) -> dict[str, int]:
    """Filter a raw Claude session JSONL to conversation-only records.

    Args:
        input_path: Path to the raw session JSONL.
        output_path: Path for the filtered output.

    Returns:
        Counts of filtered record types.
    """
    with input_path.open() as f:
        raw_lines = [json.loads(line) for line in f]

    filtered: list[dict] = []
    for record in raw_lines:
        record_type = record.get("type")

        # User messages: keep only non-meta records with string content
        # (list content = tool_result records injected by the system)
        if record_type == "user" and "message" in record and not record.get("isMeta"):
            content = record["message"].get("content", "")
            if isinstance(content, str) and content.strip():
                filtered.append({
                    "type": "user",
                    "timestamp": record.get("timestamp"),
                    "content": content,
                })

        # Assistant messages: extract text blocks only (skip tool_use)
        elif record_type == "assistant" and "message" in record:
            content = record["message"].get("content", [])
            if isinstance(content, list):
                text_parts = [
                    item["text"]
                    for item in content
                    if isinstance(item, dict)
                    and item.get("type") == "text"
                    and item.get("text", "").strip()
                ]
                if text_parts:
                    filtered.append({
                        "type": "assistant",
                        "timestamp": record.get("timestamp"),
                        "content": "\n\n".join(text_parts),
                    })

    with output_path.open("w") as f:
        for record in filtered:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    counts = {
        "raw_records": len(raw_lines),
        "filtered_records": len(filtered),
        "user": sum(1 for r in filtered if r["type"] == "user"),
        "assistant": sum(1 for r in filtered if r["type"] == "assistant"),
    }
    return counts


def _creation_timestamp_prefix() -> str:
    """Return a YYMMDD-HHMMSS- prefix using the current local time.

    This reflects when the filtered transcript was created, so filenames
    sort by creation order.
    """
    return datetime.now().strftime("%y%m%d-%H%M%S-")


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <input.jsonl> [output.jsonl]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"Error: {input_path} not found")
        sys.exit(1)

    if len(sys.argv) >= 3:
        output_path = Path(sys.argv[2])
    else:
        timestamp_prefix = _creation_timestamp_prefix()
        output_name = f"{timestamp_prefix}{input_path.stem}-filtered.jsonl"
        output_path = input_path.parent / output_name

    counts = filter_transcript(input_path, output_path)
    print(f"Filtered {counts['raw_records']} raw records -> {counts['filtered_records']} conversation records")
    print(f"  user: {counts['user']}")
    print(f"  assistant: {counts['assistant']}")
    print(f"  output: {output_path}")


if __name__ == "__main__":
    main()
