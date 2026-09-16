"""Keep the full text of every rewritten tool result so it can be recalled."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from winnow.config import Config


def key_for(session_id: str, tool_use_id: str, text: str) -> str:
    digest = hashlib.sha256()
    for part in (session_id, "\0", tool_use_id, "\0", text):
        digest.update(part.encode("utf-8", errors="replace"))
    return digest.hexdigest()[:12]


def store(cfg: Config, key: str, payload: dict[str, Any]) -> Path:
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.cache_dir / f"{key}.json"
    payload = {"key": key, "created": time.time(), **payload}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load(cfg: Config, key: str) -> dict[str, Any] | None:
    if not key.isalnum():
        return None
    path = cfg.cache_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def slice_lines(text: str, start: int | None, end: int | None, line_offset: int = 1) -> str:
    """Return lines ``start``..``end`` (inclusive, in the numbering the stub used)."""
    lines = text.split("\n")
    first = 0 if start is None else max(0, start - line_offset)
    last = len(lines) if end is None else max(first, end - line_offset + 1)
    return "\n".join(lines[first:last])
