"""Configuration, read from WINNOW_* environment variables.

Everything has a conservative default so the hook is safe to install before a
single knob is tuned. The two thresholds that matter most:

- ``drop``: a block is hidden only when P(needed) is below this. Anything
  between ``drop`` and ``keep`` is "uncertain" and is kept.
- ``keep``: the confidence at which the error gate fires. If the judge thinks
  the output shows an error with probability >= ``keep``, nothing is hidden.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = _str(name, "1" if default else "0").strip().lower()
    return value in ("1", "true", "yes", "on")


def _paths(name: str) -> tuple[Path, ...]:
    raw = _str(name, "")
    return tuple(Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip())


@dataclass(frozen=True)
class Config:
    home: Path
    judge: str
    model: str
    judge_timeout: float
    adapter_provider: str
    adapter_model: str
    tools: tuple[str, ...]
    min_chars: int
    keep: float
    drop: float
    block_lines: int
    max_blocks: int
    max_state_chars: int
    min_prune_ratio: float
    summary: bool
    summary_model: str
    summary_max_groups: int
    summary_max_chars: int
    context_dirs: tuple[Path, ...]
    context_top_k: int
    context_gate: float
    context_max_chars: int
    context_max_candidates: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            home=Path(_str("WINNOW_HOME", str(Path.home() / ".winnow"))).expanduser(),
            judge=_str("WINNOW_JUDGE", "typesafe").strip().lower(),
            model=_str("WINNOW_MODEL", "jev-latest"),
            judge_timeout=_float("WINNOW_JUDGE_TIMEOUT", 15.0),
            adapter_provider=_str("WINNOW_ADAPTER_PROVIDER", "anthropic"),
            adapter_model=_str("WINNOW_ADAPTER_MODEL", "claude-haiku-4-5"),
            tools=tuple(t.strip() for t in _str("WINNOW_TOOLS", "Read,Bash,Grep").split(",") if t.strip()),
            min_chars=_int("WINNOW_MIN_CHARS", 1500),
            keep=_float("WINNOW_KEEP", 0.5),
            drop=_float("WINNOW_DROP", 0.3),
            block_lines=_int("WINNOW_BLOCK_LINES", 25),
            max_blocks=_int("WINNOW_MAX_BLOCKS", 200),
            max_state_chars=_int("WINNOW_MAX_STATE_CHARS", 120_000),
            min_prune_ratio=_float("WINNOW_MIN_PRUNE_RATIO", 0.2),
            summary=_bool("WINNOW_SUMMARY", True),
            summary_model=_str("WINNOW_SUMMARY_MODEL", "claude-haiku-4-5"),
            summary_max_groups=_int("WINNOW_SUMMARY_MAX_GROUPS", 4),
            summary_max_chars=_int("WINNOW_SUMMARY_MAX_CHARS", 20_000),
            context_dirs=_paths("WINNOW_CONTEXT_DIRS"),
            context_top_k=_int("WINNOW_CONTEXT_TOP_K", 3),
            context_gate=_float("WINNOW_CONTEXT_GATE", 0.5),
            context_max_chars=_int("WINNOW_CONTEXT_MAX_CHARS", 8_000),
            context_max_candidates=_int("WINNOW_CONTEXT_MAX_CANDIDATES", 60),
        )

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"

    @property
    def log_path(self) -> Path:
        return self.home / "decisions.jsonl"

    @property
    def error_log_path(self) -> Path:
        return self.home / "errors.log"
