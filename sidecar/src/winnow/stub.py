"""Render the stub that replaces hidden blocks, and reassemble the output."""

from __future__ import annotations

from winnow.chunk import Block
from winnow.policy import Verdict


def render_stub(
    group: list[Block],
    key: str,
    summary: str | None,
    max_probability: float,
    line_offset: int = 1,
) -> str:
    first = group[0].start + line_offset - 1
    last = group[-1].end + line_offset - 1
    count = last - first + 1
    lines = [
        f"[winnow] Lines {first}-{last} ({count} lines) hidden: judged unlikely to matter "
        f"for the current task (relevance <= {max_probability:.2f})."
    ]
    if summary:
        lines.append(f"[winnow] Summary: {summary}")
    else:
        lines.append("[winnow] Summary unavailable.")
    lines.append(
        f"[winnow] Full text cached as key {key}. "
        f'Call winnow_recall(key="{key}", start={first}, end={last}) if you need it.'
    )
    return "\n".join(lines)


def assemble(blocks: list[Block], verdict: Verdict, stubs: dict[int, str]) -> str:
    """Rebuild the text: kept blocks verbatim, each hidden group replaced by its stub.

    ``stubs`` is keyed by the index of the first block in each hidden group.
    """
    hidden = {b.id for b in verdict.pruned}
    parts: list[str] = []
    for block in blocks:
        if block.id in hidden:
            if block.index in stubs:
                parts.append(stubs[block.index])
            continue
        parts.append(block.text)
    return "\n".join(parts)
