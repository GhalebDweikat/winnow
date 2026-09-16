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

Plus two gates: the error question (hide nothing if P(error) >= keep) and the minimum prune ratio (do not rewrite for a small saving). Start conservative, then move `drop` up as the regret rate stays low.

## Regret as the metric

Every rewrite logs its key. Every `winnow_recall` logs its key. Regret = recalled keys / pruned keys. It is the honest measure of whether the judge hides the right things, and plotted against `drop` it is a calibration curve on your own workload. Nobody has published one for Jev yet.

## Known limits

- **Line numbers.** For `Read`, stub line numbers are `startLine + index`, which matches the file when the read started at line 1. If Claude Code's `Read` output is already line-numbered, the numbers inside the text still agree.
- **Unknown output shapes pass through.** `Glob` and MCP tools that return content-block lists are untouched in v0.
- **Latency.** Each judged call adds the judge round trip (150 to 500 ms) plus one summarizer call per hidden group (capped at `WINNOW_SUMMARY_MAX_GROUPS`) plus Python startup. A resident HTTP sidecar (Claude Code supports `http` hooks) would remove the startup cost.
- **Jev limits are undocumented.** Context window and maximum questions per call are not published. `WINNOW_MAX_STATE_CHARS` and `WINNOW_MAX_BLOCKS` are guesses to tune.
- **Windows.** Hooks run under Git Bash when present. Paths from Claude Code arrive with backslashes; nothing here assumes otherwise.
- **The venv lives in `sidecar/.venv`.** A plugin's install directory changes on update; moving the environment to `${CLAUDE_PLUGIN_DATA}` would make it survive.

## Roadmap

1. **Ship v0 against the adapter**, then swap to Jev when the key arrives. Same code.
2. **Replay evaluation.** Take real transcripts, re-run the judge over every large tool result, and score against what the agent actually used afterward. Publish regret vs. threshold.
3. **Read narrowing (`PreToolUse` on `Read`).** For a large file, ask which regions answer the assistant's stated intent and rewrite the call with `offset`/`limit` via `updatedInput`. Riskier because intent is inferred; do it after the pruning data exists.
4. **Done-ness gate (`Stop`).** A Noul over the transcript tail: is the task complete? The open question is what state the judge needs: the original request, the todo list, test output, and the final assistant message are the candidates. Decide after looking at real Stop payloads.
5. **Resident sidecar.** Switch `hooks.json` to `http` hooks against a local server started by a `SessionStart` hook.
6. **Vendor-neutral judge interface.** `judge.py` already has it. Add a fine-tuned encoder backend when one is worth comparing.
