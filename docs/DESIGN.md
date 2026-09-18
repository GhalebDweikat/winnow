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

Two state bugs found by reviewing live stubs (17 Sep 2026), both fixed in 0.3.4:

- **Subagent calls were judged against the orchestrator's task.** Inside a subagent the hook's `transcript_path` is the parent session's file, so "the last user message" was whatever the human last typed to the orchestrator (often a bare "1" or "done"). The judge now reads the subagent's own transcript at `<session>/subagents/agent-<id>.jsonl`, whose first user message is the delegation prompt.
- **Long prompts were truncated from the wrong end.** A 1,500-character cap kept the *tail* of the user request, so a long delegation prompt arrived as its closing details with the ask cut off. User requests now keep their head; assistant intents keep their tail, which is where the next step is stated.

Both affected every subagent-heavy session, which for this developer is most of them. The stubs reviewed before the fix were judged under worse conditions than the stubs that will follow.

## Thresholds

| Probability | Decision |
|---|---|
| p >= keep (0.5) | keep |
| drop (0.1) <= p < keep | keep, logged as uncertain |
| p < drop | hide |

Plus two gates: the error question (hide nothing if P(error) >= keep) and the minimum prune ratio (do not rewrite for a small saving). Start conservative, then move `drop` up as the regret rate stays low. On the hand-labeled replay numbers below, `drop=0.1` is the default: it is the only bin that was clean.

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
- The observed rate never gets below 0.26 even where Jev says 0.07. The hand labels below show that this is mostly the weak label's fault, not Jev's.
- The question phrasing and the task state are the levers. This harness makes every change to the question set or to `transcript.read_task` a one-command experiment costing a few cents.

Shadow mode cannot measure regret: nothing is hidden, so nothing is recalled. Live regret needs active mode at a conservative threshold plus the recall counter.

### Hand labels: separating label noise from judge error

`winnow replay sample` drew 100 blocks from the Jev-judged cases, stratified (50 from p < 0.2, 25 from 0.2–0.5, 25 from ≥ 0.5), shuffled, and written out blind. Claude (Fable 5.1, in this repo's own session) labeled them from the task and the block alone: 47 needed, 50 not needed, 3 unsure. A 20-block audit by the human owner is the next step; until then treat these as a strong model's opinion, not ground truth.

Weak label vs hand label on the 97 decided blocks: agreement 69%. The disagreements go both ways: 20 blocks the weak label called *not needed* were needed (Claude used them without quoting them), and 10 it called *needed* were not (incidental identifier mentions). So the weak label both under- and over-marks, and its regret numbers are noisy in both directions rather than a clean upper bound.

Jev's calibration against hand labels, default question set:

| bin | n | mean p | needed rate |
|---|---|---|---|
| 0.0–0.1 | 10 | 0.07 | **0.00** |
| 0.1–0.2 | 37 | 0.15 | 0.32 |
| 0.2–0.3 | 9 | 0.24 | 0.44 |
| 0.3–0.5 | 16 | 0.42 | 0.69 |
| 0.5–0.7 | 18 | 0.57 | 0.72 |
| 0.7–1.0 | 7 | 0.80 | 1.00 |

Jev's most confident "no" bin is perfectly clean, and the ordering is right everywhere. Above 0.1 it is systematically *underconfident*: observed rates run 0.15–0.30 above the stated probability. That is a much better problem than the reverse, and it says the safe operating point is `drop=0.1`, not 0.2: the 0.1–0.2 bin is a third needed.

### Question sets, measured

Three phrasings of the per-block question, same 300 cases, same judge, scored on the hand labels at the operating point:

| set | blocks p < 0.1 | needed among them | text hidden at 0.1 (population, weak label) | ECE (hand) |
|---|---|---|---|---|
| `default` (one-line criteria) | 10 | 0% | 1.8% | 0.18 |
| `structured` (what / not_for / examples) | 23 | **0%** | **4.6%** | 0.26 |
| `strict` ("directly about the task") | 35 | 23% | 10.9% | 0.28 |

`structured` moves more than twice as much text below the safe threshold while keeping that bin clean; `strict` makes Jev overconfident and unsafe. `structured` is now the default (`WINNOW_QUESTIONS`). Its worse ECE is all in the middle bins, where it is even more underconfident, which does not matter for a 0.1 threshold. Raising the threshold safely means fixing that underconfidence, which is the next tuning target; candidates are richer task state (todo list, last edited file) and a Score over relevance levels.

Expected live effect at `drop=0.1` with `structured`: about 5% of large-result text hidden, at zero hand-label regret. Modest, honest, and the number to beat.

## Latency, measured

`winnow bench` on this machine (Windows 11, Python 3.14, warm disk):

| Path | Median |
|---|---|
| interpreter only | 92 ms |
| small result, `python -m winnow` (fast path, no SDK import) | 274 ms |
| small result via `uv run` (what Claude Code runs) | 374 ms |
| interpreter + `import typesafe_sdk` | 566 ms |

So via command hooks a result under `WINNOW_MIN_CHARS` costs about 370 ms, and a judged result about 850 ms before the request leaves, of which about 470 ms is importing the SDK (mostly `httpx2` reading package metadata).

### The resident sidecar

`winnow serve` is a loopback HTTP server that keeps the SDK imported and the judge's HTTP client warm. Claude Code's `http` hooks POST the event JSON to it and read the hook output from the response body: an empty 2xx is pass-through, a JSON 2xx is the hook output, and a connection failure is a non-blocking error. The SessionStart hook runs `winnow serve --ensure`, which spawns a detached server if none answers (and replaces one from an older plugin version). The server re-reads `~/.winnow/env` when it changes, so a key or threshold edit takes effect without a restart, and exits after 45 idle minutes.

| Path | Median |
|---|---|
| small result via `uv run` command hook | 381 ms |
| small result via the sidecar | **16 ms** |

Judged results also skip the ~500 ms SDK import, and TLS reuse takes the judge round trip itself from the 300–500 ms range down toward the 90 ms Jev shows in replay. If the sidecar is down, results pass through unjudged and Claude Code shows the hook error; `winnow doctor` and `winnow serve --status` both report it.

### Function hooks: the same judge, in-process

Claude Code's function hooks (early access, `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`, 2.1.260+) load a TypeScript module into the engine. A `tool.call` handler there sees the call, runs the tool with `next(e)`, and returns `{ result }`: the structured result Claude will read, with no JSON-on-stdout contract and no transcript path. winnow's module (`hooks/winnow.ts`) keeps the judge in the Python sidecar and changes three things:

- **Task state from the live session.** `$.session.messages()` gives the conversation as the engine holds it, so the "current task" is the real last human request and the assistant's last sentence, with no file lag. Inside a subagent the messages are the subagent's own, which closes the state bug that 0.3.4 fixed on the http path by guessing the subagent's transcript file.
- **Visible decisions.** The sidecar puts a summary of each rewrite in an `X-Winnow` response header (blocks hidden, characters before and after, the recall key) and the module shows it as a toast. Http hooks have no channel for that.
- **No double judging.** Both paths fire for the same call when the flag is on, and the http hook may arrive with the already-rewritten text. The sidecar keys its last 512 answers by `tool_use_id` and returns the same answer for a repeat, so the rewrite is idempotent whichever path reaches Claude last; a prompt seen again within 30 s gets nothing back, since context must be injected once.

Everything that was measured stays measured: the same extraction, chunking, questions, thresholds and cache, so the calibration numbers above apply unchanged. The module's own tests run under `claude plugin test`, which loads the plugin into an engine with no network, so they cover task reconstruction and the pass-through path (sidecar down, small result, denied call). A live session with the flag on had not been run when this was written; the first one should confirm that the toast appears, that `deduped` climbs in `winnow serve --status`, and that decision lines carry `"source": "function-hook"`.

The engine also offers `session.compact` (2.1.274+), where a hook can replace the compaction result. That is the layer fast-jev-compaction works at; winnow's calibrated block question could run there over whole results before the summary, and the harness can tell whether it should.

## Known limits

- **Line numbers.** For `Read`, stub line numbers are `startLine + index`, which matches the file when the read started at line 1. If Claude Code's `Read` output is already line-numbered, the numbers inside the text still agree.
- **Unknown output shapes pass through.** `Glob` and MCP tools that return content-block lists are untouched in v0.
- **Latency.** Each judged call adds the judge round trip (150 to 500 ms) plus one summarizer call per hidden group (capped at `WINNOW_SUMMARY_MAX_GROUPS`) plus the startup cost measured below. A resident HTTP sidecar (Claude Code supports `http` hooks) would remove the startup cost.
- **Jev limits are undocumented.** Context window and maximum questions per call are not published. `WINNOW_MAX_STATE_CHARS` and `WINNOW_MAX_BLOCKS` are guesses to tune.
- **Windows.** Hooks run under Git Bash when present. Paths from Claude Code arrive with backslashes; nothing here assumes otherwise.
- **The venv lives in `sidecar/.venv`.** A plugin's install directory changes on update; moving the environment to `${CLAUDE_PLUGIN_DATA}` would make it survive.
- **Long-lived processes must not run through the `winnow.exe` launcher.** On Windows a console-script launcher holds its own executable open for as long as the script runs. The per-session MCP server and the sidecar therefore start as `python -m winnow ...`; otherwise the next version bump's editable rebuild fails with "file in use" and every `uv run` in that environment fails with it, hooks included. Found the hard way on 0.3.0.

## Roadmap

1. **Ship v0 against the adapter**, then swap to Jev when the key arrives. Same code. (Blocked on any key.)
2. **Replay evaluation.** Done offline (`winnow replay`); publish regret vs. threshold once a real judge has been run over the cases.
3. **Read narrowing (`PreToolUse` on `Read`).** For a large file, ask which regions answer the assistant's stated intent and rewrite the call with `offset`/`limit` via `updatedInput`. Riskier because intent is inferred; do it after the pruning data exists.
4. **Done-ness gate (`Stop`).** A Noul (TypeSafe's yes/no question type, answered with a probability) over the transcript tail: is the task complete? The open question is what state the judge needs: the original request, the todo list, test output, and the final assistant message are the candidates. Decide after looking at real Stop payloads.
5. **Resident sidecar.** Done: `http` hooks against `winnow serve`, started by SessionStart. 381 ms → 16 ms per small result.
7. **Bigger, human-audited label set.** 97 model-labeled blocks is enough to pick an operating point, not enough to publish a curve. The first 20-block human audit disagreed with the model labels (38% agreement) and with the weak label and Jev's ordering alike; the disagreement traced to an underspecified criterion ("Claude had to look at it to know it was irrelevant" counts every block as needed) and to a task excerpt too thin for someone who no longer remembers the session. The labeling sheet now states the criterion; the next audit should show what Claude did next, since a human judging ground truth may use hindsight even though the judge cannot.
8. **Live regret, with a human.** Active mode at `drop=0.1` with the recall counter is running. `winnow review` adds the number that matters: the owner judges recent stubs while the session is fresh, and `winnow stats` reports human regret alongside recall regret. First pass (17 Sep 2026): 7 stubs reviewed, 6 of 6 genuine ones fine, the seventh a synthetic test file reviewed without its task; every hidden block was at or below 0.10. Next: thirty clean reviews before claiming zero regret, then a week at `drop=0.15` with the same review.
9. **Digests instead of silence.** With summaries off, a stub used to say only "25 lines hidden". A hidden Grep result now names the files and counts; other tools get line count, shape (comments, imports, repetition) and the first line. Deterministic, no model.
6. **Vendor-neutral judge interface.** `judge.py` already has it. Add a fine-tuned encoder backend when one is worth comparing.
10. **Function-hook mode.** Done in 0.4.0 (`hooks/winnow.ts`): task from the live session, toast per rewrite, dedupe against the http path. Next: a live session with the flag on, then a `session.compact` pass that judges whole results at compaction time with the same calibrated question.
