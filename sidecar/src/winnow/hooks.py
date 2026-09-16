"""The two hook handlers. Each takes the hook payload Claude Code sends on stdin
and returns the JSON to print on stdout, or ``None`` to pass through.

Both are written so that any failure (no API key, judge down, odd tool shape)
degrades to pass-through. winnow must never be the reason a tool result went
missing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from typesafe_sdk import Noul

from winnow import cache, log
from winnow.chunk import Block, chunk, group_contiguous
from winnow.config import Config
from winnow.extract import extract, trimmed_input
from winnow.judge import Judge, build_judge
from winnow.memory import load_candidates
from winnow.policy import decide
from winnow.stub import assemble, render_stub
from winnow.summarize import Summarizer, build_summarizer
from winnow.transcript import Task, read_task


@dataclass
class Runtime:
    cfg: Config
    judge: Judge | None
    summarizer: Summarizer | None

    @classmethod
    def from_config(cls, cfg: Config) -> "Runtime":
        return cls(cfg, build_judge(cfg), build_summarizer(cfg))


# --------------------------------------------------------------------------- #
# PostToolUse: judge each block of a large tool result                        #
# --------------------------------------------------------------------------- #

BLOCK_CRITERIA = {
    "true": (
        "The block holds content the task depends on: matching code or text, results, "
        "values the user asked for, definitions being edited, or anything referenced by the task."
    ),
    "false": (
        "The block is boilerplate, unrelated to the task, repetitive noise, or something "
        "the task does not depend on."
    ),
}


def _block_questions(blocks: list[Block]) -> dict[str, Noul]:
    questions: dict[str, Noul] = {
        block.id: Noul(
            instructions=f"Is `blocks.{block.id}` needed to accomplish `task`? Judge it against `task` and `tool`.",
            criteria=BLOCK_CRITERIA,
        )
        for block in blocks
    }
    questions["error_present"] = Noul(
        instructions=(
            "Does the tool output (the whole of `blocks`) show an error, failure, warning, "
            "or unexpected result that the assistant needs to know about?"
        ),
        criteria={
            "true": "Tracebacks, non-zero exits, 'not found', permission errors, failing tests, or output that contradicts what `task` expected.",
            "false": "Ordinary successful output.",
        },
    )
    return questions


def _judge_window(blocks: list[Block], max_chars: int) -> list[Block]:
    """The prefix of blocks that fits the state budget. The rest is kept unjudged."""
    window: list[Block] = []
    used = 0
    for block in blocks:
        used += len(block.text)
        if used > max_chars and window:
            break
        window.append(block)
    return window


def post_tool_use(payload: dict[str, Any], runtime: Runtime) -> dict[str, Any] | None:
    cfg = runtime.cfg
    tool_name = str(payload.get("tool_name") or "")
    if tool_name not in cfg.tools or runtime.judge is None:
        return None

    tool_response = payload.get("tool_response", payload.get("tool_output"))
    extracted = extract(tool_name, payload.get("tool_input"), tool_response)
    if extracted is None or len(extracted.text) < cfg.min_chars:
        return None

    blocks = chunk(extracted.text, block_lines=cfg.block_lines, max_blocks=cfg.max_blocks)
    if len(blocks) < 2:
        return None

    task = read_task(payload.get("transcript_path"))
    judged = _judge_window(blocks, cfg.max_state_chars)
    state = {
        "task": task.as_state(),
        "tool": {"name": tool_name, "input": trimmed_input(tool_name, payload.get("tool_input"))},
        "blocks": {block.id: block.text for block in judged},
    }

    session_id = str(payload.get("session_id") or "")
    tool_use_id = str(payload.get("tool_use_id") or "")
    event: dict[str, Any] = {
        "event": "post_tool_use",
        "session_id": session_id,
        "tool_use_id": tool_use_id,
        "tool": tool_name,
        "describe": extracted.describe,
        "n_blocks": len(blocks),
        "n_judged": len(judged),
        "chars_before": len(extracted.text),
        "task_known": not task.is_empty,
        "judge": runtime.judge.name,
    }

    try:
        result = runtime.judge.nouls(state, _block_questions(judged))
    except Exception as exc:  # noqa: BLE001 - any judge failure means pass through
        log.log_error(cfg, "judge", exc)
        log.log_event(cfg, {**event, "rewritten": False, "reason": "judge_error", "error": repr(exc)})
        return None

    event.update(
        judge_model=result.model,
        judge_ms=result.latency_ms,
        judge_input_tokens=result.input_tokens,
        probabilities=result.probabilities,
    )
    verdict = decide(blocks, result.probabilities, result.probabilities.get("error_present"), cfg)
    if not verdict.pruned:
        log.log_event(cfg, {**event, "rewritten": False, "reason": verdict.reason})
        return None

    key = cache.key_for(session_id, tool_use_id, extracted.text)
    cache.store(
        cfg,
        key,
        {
            "tool": tool_name,
            "describe": extracted.describe,
            "session_id": session_id,
            "tool_use_id": tool_use_id,
            "line_offset": extracted.line_offset,
            "text": extracted.text,
            "blocks": [
                {"id": b.id, "start": b.start, "end": b.end, "p": result.probabilities.get(b.id), "hidden": b in verdict.pruned}
                for b in blocks
            ],
        },
    )

    stubs: dict[int, str] = {}
    summary_ms = 0
    for n, group in enumerate(group_contiguous(verdict.pruned)):
        text = "\n".join(b.text for b in group)
        summary = None
        if runtime.summarizer is not None and n < cfg.summary_max_groups:
            started = time.perf_counter()
            try:
                summary = runtime.summarizer.summarize(text[: cfg.summary_max_chars], task, extracted.describe)
            except Exception as exc:  # noqa: BLE001 - a missing summary is not fatal
                log.log_error(cfg, "summarizer", exc)
            summary_ms += int((time.perf_counter() - started) * 1000)
        max_p = max((result.probabilities.get(b.id, 0.0) for b in group), default=0.0)
        stubs[group[0].index] = render_stub(group, key, summary, max_p, extracted.line_offset)

    new_text = assemble(blocks, verdict, stubs)
    log.log_event(
        cfg,
        {
            **event,
            "rewritten": True,
            "reason": verdict.reason,
            "key": key,
            "n_hidden": len(verdict.pruned),
            "n_uncertain": len(verdict.uncertain),
            "chars_after": len(new_text),
            "summary_ms": summary_ms if runtime.summarizer else None,
        },
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": extracted.rebuild(new_text),
        }
    }


# --------------------------------------------------------------------------- #
# UserPromptSubmit: inject the context files this prompt actually needs       #
# --------------------------------------------------------------------------- #


def user_prompt_submit(payload: dict[str, Any], runtime: Runtime) -> dict[str, Any] | None:
    cfg = runtime.cfg
    prompt = str(payload.get("prompt") or payload.get("prompt_text") or "").strip()
    if len(prompt) < 12 or runtime.judge is None:
        return None

    candidates = load_candidates(cfg, str(payload.get("cwd") or ""))
    if not candidates:
        return None

    state = {
        "prompt": prompt[:4000],
        "candidates": {
            c.id: {"title": c.title, "description": c.description, "excerpt": c.text[:600]}
            for c in candidates
        },
    }
    questions = {
        c.id: Noul(
            instructions=f"Would the assistant need to read `candidates.{c.id}` to handle `prompt` well?",
            criteria={
                "true": "The file records facts, preferences, decisions, or project state that `prompt` depends on or touches.",
                "false": "The file is about something else; the prompt can be handled without it.",
            },
        )
        for c in candidates
    }

    event: dict[str, Any] = {
        "event": "user_prompt_submit",
        "session_id": str(payload.get("session_id") or ""),
        "n_candidates": len(candidates),
        "judge": runtime.judge.name,
    }
    try:
        result = runtime.judge.nouls(state, questions)
    except Exception as exc:  # noqa: BLE001
        log.log_error(cfg, "judge", exc)
        log.log_event(cfg, {**event, "injected": False, "reason": "judge_error", "error": repr(exc)})
        return None

    ranked = sorted(candidates, key=lambda c: result.probabilities.get(c.id, 0.0), reverse=True)
    chosen = [c for c in ranked if result.probabilities.get(c.id, 0.0) >= cfg.context_gate][: cfg.context_top_k]
    event.update(judge_ms=result.latency_ms, judge_input_tokens=result.input_tokens, probabilities=result.probabilities)
    if not chosen:
        log.log_event(cfg, {**event, "injected": False, "reason": "below_gate"})
        return None

    parts = ["winnow selected these files as relevant to this prompt (read them here instead of opening them):"]
    budget = cfg.context_max_chars - len(parts[0])
    for c in chosen:
        header = f"\n\n### {c.title} ({c.path})\n"
        room = budget - len(header)
        if room <= 200:
            break
        body = c.text if len(c.text) <= room else c.text[: room - 15] + "\n[truncated]"
        parts.append(header + body)
        budget -= len(header) + len(body)

    log.log_event(cfg, {**event, "injected": True, "reason": "injected", "chosen": [c.id for c in chosen]})
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "".join(parts),
        }
    }
