"""Derive the "current task" from a Claude Code session transcript.

This is the state-engineering heart of winnow. The judge cannot decide whether a
block matters without knowing what the agent is trying to do, and Claude Code
does not hand the hook that information directly. We reconstruct it from the
tail of the session transcript (a JSONL file whose path arrives in every hook
payload): the last thing the user asked for, and the last thing the assistant
said it was about to do.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    user_request: str = ""
    assistant_intent: str = ""

    def as_state(self) -> dict[str, str]:
        return {
            "user_request": self.user_request,
            "assistant_intent": self.assistant_intent,
        }

    @property
    def is_empty(self) -> bool:
        return not (self.user_request or self.assistant_intent)


def _tail_bytes(path: str, max_bytes: int) -> bytes:
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()  # discard the partial line we landed in
        return fh.read()


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def _has_tool_result(content: object) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def read_task(
    transcript_path: str | None,
    *,
    max_chars: int = 1500,
    max_bytes: int = 2_000_000,
) -> Task:
    """Return the latest user request and assistant intent from the transcript tail."""
    if not transcript_path or not os.path.exists(transcript_path):
        return Task()
    try:
        data = _tail_bytes(transcript_path, max_bytes)
    except OSError:
        return Task()

    user, assistant = "", ""
    for line in data.decode("utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        message = entry.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if kind == "user":
            if entry.get("isMeta") or _has_tool_result(content):
                continue
            text = _text_of(content).strip()
            if text:
                user, assistant = text, ""
        elif kind == "assistant":
            text = _text_of(content).strip()
            if text:
                assistant = text
    return Task(user[-max_chars:], assistant[-max_chars:])
