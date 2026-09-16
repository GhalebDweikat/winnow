# Design notes

## The idea in one line

Claude Code's context fills with tool output the task never needed. A System One model can answer "is this block needed?" for a hundred blocks in one call, with a probability instead of a guess. winnow puts that judgment between the tool and the context.

## Why a judge and not a summarizer

Existing Claude Code context plugins decide what to keep with byte thresholds, duplicate counters, or an LLM summarizing the whole output. Thresholds are blind to relevance. Summarizing everything is lossy and slow. A calibrated per-block probability lets code make the keep/hide decision deterministically, and lets the threshold be tuned against a measurable regret rate. The summarizer is still there, but it only runs on what the judge already decided to hide, so it is cheap and its mistakes are recoverable.

## Hook mechanics that shaped the design

- `PostToolUse` can replace a tool's result via `hookSpecificOutput.updatedToolOutput` (Claude Code 2.1.121+, all tools). The replacement must match the tool's output shape exactly or it is silently ignored. That is why `extract.py` has one explicit rebuilder per tool.
- `UserPromptSubmit` and `SessionStart` can inject `additionalContext`. Hook output strings are capped at 10,000 characters, so the prompt-time selector budgets `WINNOW_CONTEXT_MAX_CHARS` below that.
- `PreCompact` can only block compaction, not shape the summary. Compaction stays Anthropic's.
- MCP tool schemas are already deferred natively when they exceed 10% of context (tool search). winnow does not touch tool selection.
- Hooks see `transcript_path`. That is how winnow recovers what the agent is doing.

## State engineering

TypeSafe's CEO called state engineering the hard part, and it is. The judge's state is:

```json
{
  "task":   {"user_request": "...", "assistant_intent": "..."},
  "tool":   {"name": "Read", "input": {"file_path": "..."}},
  "blocks": {"b001": "...", "b002": "..."}
}
```

`task` is reconstructed from the transcript tail: the last non-meta user message and the last assistant text before the tool call. A new user turn resets the intent. This is v0; better signals are the current todo list, the last edited file, and the names the assistant mentioned in its intent.

Questions reference the state by path, as TypeSafe recommends, so the model reads the data rather than its priors:

> Is `blocks.b007` needed to accomplish `task`? Judge it against `task` and `tool`.

## Thresholds

| Probability | Decision |
|---|---|
| p >= keep (0.5) | keep |
| drop (0.3) <= p < keep | keep, logged as uncertain |
| p < drop | hide |

Plus two gates: the error question (hide nothing if P(error) >= keep) and the minimum prune ratio (do not rewrite for a small saving). Start conservative, then move `drop` up as the regret rate stays low. On the first replay numbers below, `drop=0.2` is the conservative starting point for active mode, not 0.3.

## Regret as the metric

Every rewrite logs its key. Every `winnow_recall` logs its key. Regret = recalled keys / pruned keys. It is the honest measure of whether the judge hides the right things, and plotted against `drop` it is a calibration curve on your own workload. Nobody has published one for Jev yet.

Shadow mode (`WINNOW_MODE=shadow`) collects the same decisions without changing anything Claude sees, so the log fills up before anyone has to trust the judge.

## Replay: weak labels from your own history

`winnow replay` builds the benchmark offline from Claude Code transcripts. For every large `Read`/`Bash`/`Grep` result it records the task at that moment (last user request, last assistant sentence) and the next 12 assistant events (`--window`). Each 25-line block is then labeled:

| Label | Rule |
|---|---|
| needed / line | a significant line of the block (12+ chars, 6+ alphanumerics, whitespace-normalized) reappears in any later assistant text, edit, write, or command |
| needed / ident | a distinctive identifier (underscore, digit, inner capital, or 10+ chars) that appears in at most two blocks of the output is mentioned in later prose, a command, a grep, or an edit's `old_string` |
| not_needed / none | neither |
| unknown | no assistant events followed the result before the next human turn |

Design choices that matter: `Write` content and edit `new_string` count for line overlap but not for identifier mentions, because rewriting a file would otherwise mark every block of it as needed through incidental names; the window stops long autonomous turns from making everything "used eventually"; subagent (sidechain) entries are ignored.

Known bias: Claude can read a block, use it to understand the code, and never quote it. Those blocks are labeled not needed, so **regret from replay is an upper bound** and savings an estimate. Per-block `reason` is recorded so the label mix can be audited.

### First numbers: Jev vs the lexical baseline

300 cases from this machine's transcripts (16 Sep 2026, window 12, 1,515 labeled blocks, needed fraction 0.48; reasons line 271 / ident 458 / none 786). Both judges saw identical cases. Raw score files are in `docs/results/2026-09-16/`.

| | Jev (`jev-latest`) | lexical baseline |
|---|---|---|
| Expected calibration error | **0.14** | 0.31 |
| Median latency per case | 86 ms | n/a |
| Cost for all 300 cases | $0.036 (863k input tokens) | 0 |
| At `drop=0.3`: text hidden | 22.6% | 81.5% |
| At `drop=0.3`: regret (upper bound) | 21.3% | 85.8% |
| At `drop=0.3`: hidden precision | 59% | 26% |

Jev's calibration table (mean predicted P(needed) vs observed rate under the weak label):

| bin | n | mean p | needed rate |
|---|---|---|---|
| 0.0–0.1 | 39 | 0.07 | 0.26 |
| 0.1–0.2 | 126 | 0.15 | 0.38 |
| 0.2–0.3 | 216 | 0.24 | 0.45 |
| 0.3–0.5 | 408 | 0.40 | 0.49 |
| 0.5–0.8 | 598 | 0.63 | 0.51 |
| 0.8–1.0 | 128 | 0.84 | 0.58 |

Reading it honestly:

- Jev is a real judge and the baseline is not; the ordering is right and the low bins are genuinely lower. That is the headline.
- The observed rate never gets below 0.26 even where Jev says 0.07. Part of that is the weak label's known over-marking (the `ident` rule fires more than the `line` rule); part may be Jev spreading probability across the 0.2–0.6 range for this question. The two can be separated by hand-labeling a sample of the 0.0–0.2 bin.
- The default `drop=0.3` is too aggressive for active mode on this evidence. `drop=0.2` hides about 10% of text at 8% regret; `drop=0.1` about 2% at 1.4%. Start there and move up as hand-checked regret stays low.
- The question phrasing and the task state are the levers. This harness makes every change to `_block_questions` or `transcript.read_task` a one-command experiment costing a few cents.

Shadow mode cannot measure regret: nothing is hidden, so nothing is recalled. Live regret needs active mode at a conservative threshold plus the recall counter.

## Latency, measured

`winnow bench` on this machine (Windows 11, Python 3.14, warm disk):

| Path | Median |
|---|---|
| interpreter only | 92 ms |
| small result, `python -m winnow` (fast path, no SDK import) | 274 ms |
| small result via `uv run` (what Claude Code runs) | 374 ms |
| interpreter + `import typesafe_sdk` | 566 ms |

So a result under `WINNOW_MIN_CHARS` costs about 370 ms, and a judged result about 850 ms before the request leaves, of which about 470 ms is importing the SDK (mostly `httpx2` reading package metadata). The fast path exists because most tool results are small. The remaining fast-path cost is stdlib imports (`dataclasses`, `argparse`, `hashlib`, `pathlib`). A resident sidecar behind an `http` hook would reduce both to a local round trip and is the next latency step.

## Known limits

- **Line numbers.** For `Read`, stub line numbers are `startLine + index`, which matches the file when the read started at line 1. If Claude Code's `Read` output is already line-numbered, the numbers inside the text still agree.
- **Unknown output shapes pass through.** `Glob` and MCP tools that return content-block lists are untouched in v0.
- **Latency.** Each judged call adds the judge round trip (150 to 500 ms) plus one summarizer call per hidden group (capped at `WINNOW_SUMMARY_MAX_GROUPS`) plus the startup cost measured below. A resident HTTP sidecar (Claude Code supports `http` hooks) would remove the startup cost.
- **Jev limits are undocumented.** Context window and maximum questions per call are not published. `WINNOW_MAX_STATE_CHARS` and `WINNOW_MAX_BLOCKS` are guesses to tune.
- **Windows.** Hooks run under Git Bash when present. Paths from Claude Code arrive with backslashes; nothing here assumes otherwise.
- **The venv lives in `sidecar/.venv`.** A plugin's install directory changes on update; moving the environment to `${CLAUDE_PLUGIN_DATA}` would make it survive.

## Roadmap

1. **Ship v0 against the adapter**, then swap to Jev when the key arrives. Same code. (Blocked on any key.)
2. **Replay evaluation.** Done offline (`winnow replay`); publish regret vs. threshold once a real judge has been run over the cases.
3. **Read narrowing (`PreToolUse` on `Read`).** For a large file, ask which regions answer the assistant's stated intent and rewrite the call with `offset`/`limit` via `updatedInput`. Riskier because intent is inferred; do it after the pruning data exists.
4. **Done-ness gate (`Stop`).** A Noul (TypeSafe's yes/no question type, answered with a probability) over the transcript tail: is the task complete? The open question is what state the judge needs: the original request, the todo list, test output, and the final assistant message are the candidates. Decide after looking at real Stop payloads.
5. **Resident sidecar.** Switch `hooks.json` to `http` hooks against a local server started by a `SessionStart` hook. Worth about 300 ms per small result and 800 ms per judged one on the numbers above.
6. **Vendor-neutral judge interface.** `judge.py` already has it. Add a fine-tuned encoder backend when one is worth comparing.
