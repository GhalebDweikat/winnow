"""``winnow review``: judge recent stubs while the session is still in your head.

Replay labels old transcripts from a 600-character task excerpt, which is hard
for a person who no longer remembers the session. This is the other way round:
walk the stubs winnow produced today, newest first, show exactly what was
hidden and what the task was, and ask one question per stub. The answers are
human regret, recorded in ``~/.winnow/review.jsonl`` and reported by
``winnow stats``.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from winnow import cache, log
from winnow.config import Config

VERDICTS = {"y": "fine", "x": "should_have_kept", "u": "unsure"}


def reviewed_keys(cfg: Config) -> set[str]:
    path = cfg.home / "review.jsonl"
    keys: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("key"):
                keys.add(str(entry["key"]))
    return keys


def candidates(cfg: Config, *, limit: int, since_days: float, session: str | None = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Recent rewritten decisions with their cache entries, newest first, not yet reviewed."""
    cutoff = time.time() - since_days * 86400
    seen = reviewed_keys(cfg)
    events = [
        e
        for e in log.read_events(cfg)
        if e.get("event") == "post_tool_use"
        and e.get("rewritten")
        and not e.get("demo")
        and e.get("key")
        and float(e.get("ts") or 0) >= cutoff
        and str(e["key"]) not in seen
        and (session is None or e.get("session_id") == session)
    ]
    events.sort(key=lambda e: float(e.get("ts") or 0), reverse=True)
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for event in events:
        entry = cache.load(cfg, str(event["key"]))
        if entry is not None:
            out.append((event, entry))
        if len(out) >= limit:
            break
    return out


def hidden_groups(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Consecutive hidden blocks of a cache entry, with their text and original line numbers."""
    lines = str(entry.get("text", "")).split("\n")
    offset = int(entry.get("line_offset") or 1)
    groups: list[dict[str, Any]] = []
    for block in entry.get("blocks", []):
        if not block.get("hidden"):
            continue
        start, end = int(block["start"]), int(block["end"])
        if groups and groups[-1]["end_idx"] == start - 1:
            groups[-1]["end_idx"] = end
        else:
            groups.append({"start_idx": start, "end_idx": end})
    for g in groups:
        g["text"] = "\n".join(lines[g["start_idx"] - 1 : g["end_idx"]])
        g["start"] = g["start_idx"] + offset - 1
        g["end"] = g["end_idx"] + offset - 1
    return groups


def record(cfg: Config, *, key: str, verdict: str, reviewer: str, event: dict[str, Any]) -> None:
    cfg.home.mkdir(parents=True, exist_ok=True)
    with (cfg.home / "review.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": time.time(),
                    "key": key,
                    "verdict": verdict,
                    "reviewer": reviewer,
                    "session_id": event.get("session_id"),
                    "tool": event.get("tool"),
                    "decision_ts": event.get("ts"),
                    "questions": event.get("questions"),
                    "n_hidden": event.get("n_hidden"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def run_review(
    cfg: Config,
    *,
    limit: int = 10,
    reviewer: str = "human",
    since_days: float = 7,
    session: str | None = None,
    max_lines: int = 40,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> int:
    items = candidates(cfg, limit=limit, since_days=since_days, session=session)
    if not items:
        print_fn(f"No unreviewed stubs from the last {since_days:g} days. Nothing was hidden, or you've reviewed it all.")
        return 0
    print_fn(f"{len(items)} stubs to review. y = hiding it was fine, x = it should have been kept, u = unsure, s = skip, q = quit.")
    saved = 0
    for i, (event, entry) in enumerate(items, 1):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(event.get("ts") or 0)))
        session_id = str(event.get("session_id") or "")[:8]
        print_fn("")
        print_fn(f"[{i}/{len(items)}]  {entry.get('describe') or event.get('tool')}   ({when}, session {session_id})")
        print_fn(f"       {event.get('n_hidden')} of {event.get('n_blocks')} blocks hidden, questions={event.get('questions', '?')}, {int(event.get('chars_before') or 0)} -> {int(event.get('chars_after') or 0)} chars")
        task = entry.get("task") or {}
        if task.get("user_request"):
            print_fn(f"User asked: {task['user_request'][:400]}")
        if task.get("assistant_intent"):
            print_fn(f"Claude was about to: {task['assistant_intent'][:300]}")
        if not task:
            print_fn("(task not recorded for this entry)")
        for g in hidden_groups(entry):
            print_fn("-" * 72)
            print_fn(f"hidden lines {g['start']}-{g['end']}:")
            text_lines = g["text"].splitlines()
            print_fn("\n".join(text_lines[:max_lines]) + (f"\n... ({len(text_lines) - max_lines} more lines)" if len(text_lines) > max_lines else ""))
        print_fn("-" * 72)
        while True:
            answer = input_fn("was hiding this fine? [y/x/u/s/q] ").strip().lower()
            if answer == "q":
                print_fn(f"{saved} reviews saved.")
                return saved
            if answer == "s":
                break
            verdict = VERDICTS.get(answer)
            if verdict:
                record(cfg, key=str(event["key"]), verdict=verdict, reviewer=reviewer, event=event)
                saved += 1
                break
            print_fn("y, x, u, s, or q")
    print_fn(f"{saved} reviews saved to {cfg.home / 'review.jsonl'}. `winnow stats` reports human regret.")
    return saved


def review_stats(cfg: Config) -> dict[str, Any]:
    path = cfg.home / "review.jsonl"
    counts = {"fine": 0, "should_have_kept": 0, "unsure": 0}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            v = entry.get("verdict") if isinstance(entry, dict) else None
            if v in counts:
                counts[v] += 1
    decided = counts["fine"] + counts["should_have_kept"]
    return {
        "human_reviewed": sum(counts.values()),
        "human_fine": counts["fine"],
        "human_should_have_kept": counts["should_have_kept"],
        "human_unsure": counts["unsure"],
        "human_regret_rate": round(counts["should_have_kept"] / decided, 4) if decided else None,
    }
