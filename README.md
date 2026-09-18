# winnow

[![tests](https://github.com/GhalebDweikat/winnow/actions/workflows/tests.yml/badge.svg)](https://github.com/GhalebDweikat/winnow/actions/workflows/tests.yml)

A calibrated context sieve for Claude Code.

Every large `Read`, `Bash`, or `Grep` result is judged before it enters Claude's context. Blocks the judge is confident you don't need are replaced with a three-line stub: what was hidden, a one-paragraph summary from a cheap model, and a key that restores the full text on demand. Nothing is lost; it just stops costing tokens until you ask for it.

**Terms used below.** The *judge* is the model that answers one yes/no question per block ("is this block needed for the current task?") with a probability. By default that is **Jev**, TypeSafe AI's *System One* model: a model that returns calibrated probabilities for typed questions instead of generating text, so a hundred questions come back in one call in a few hundred milliseconds. Jev is in early access. The *adapter* is TypeSafe's `system-one-adapter` package, which answers the same questions by prompting Claude Haiku 4.5; it is not calibrated, but it lets the whole pipeline run today.

## What it does

```
Read big.py  ──►  Claude Code  ──►  PostToolUse hook  ──►  winnow
                                                             │
        split into ~25-line blocks ◄─────────────────────────┘
        one call to the judge: "is block N needed for the current task?"  ×N, in parallel
        keep confident-yes and uncertain blocks verbatim
        hide confident-no blocks:  cache full text  ─►  summarize  ─►  stub
                                                             │
Claude sees ◄──  updatedToolOutput  ◄────────────────────────┘
```

A stub looks like this:

```
[winnow] Lines 41-188 (148 lines) hidden: judged unlikely to matter for the current task (relevance <= 0.22).
[winnow] Summary: Argparse setup for the --export and --format flags, plus the license header.
[winnow] Full text cached as key a1b2c3d4e5f6. Call winnow_recall(key="a1b2c3d4e5f6", start=41, end=188) if you need it.
```

Without a summarizer the middle line is a deterministic digest instead, so Claude still knows what kind of thing it lost: for a search result, which files the hidden matches came from and how many each; for anything else, the line count, whether it was mostly comments, imports or repetition, and the first line.

Two safety rules are built in. If the judge thinks the output shows an error, nothing is hidden. If a block's probability is merely uncertain (between `WINNOW_DROP` and `WINNOW_KEEP`), it is kept. Both thresholds are tunable; the rules themselves are not optional. The default `WINNOW_DROP` of 0.1 is the bin that came back clean on hand-labeled replay (see [Measured](#measured)); raise it only with your own evidence.

The hooks talk to a small resident server (`winnow serve`) on loopback, started at session start, so a hook costs about 16 ms plus the judge call rather than a Python startup. winnow never judges its own files or its own commands, so recalls and labeling sheets always come back whole.

A second hook runs at prompt time. It ranks the memory files Claude Code keeps for the project (`~/.claude/projects/<project>/memory/*.md`, everything except the `MEMORY.md` index, which Claude already loads) plus any directories in `WINNOW_CONTEXT_DIRS` against your prompt, and injects the relevant ones so Claude reads what it needs without a round of `Read` calls.

What winnow changes is only what Claude sees. Files on disk, the commands that ran, and Claude Code's own transcript are untouched.

## Quick start

Requirements: Python 3.10+, [uv](https://docs.astral.sh/uv/), Claude Code 2.1.121 or newer (2.1.260 or newer for the in-process [function-hook mode](#function-hooks-early-access)).

```bash
git clone https://github.com/GhalebDweikat/winnow.git
claude plugin marketplace add ./winnow
claude plugin install winnow@winnow
```

Then add a key (next section), open a new Claude Code session, and read any file longer than about 1,500 characters. If a `[winnow]` line appears in the result, it's working. If not, see [Troubleshooting](#troubleshooting).

Before you have any key, you can still see what it does:

```bash
uv run --project winnow/sidecar winnow demo --fake
```

That runs a synthetic 130-line file through the real pipeline with a keyword judge and prints what Claude would have seen. Once a key is set, drop `--fake` and the same command makes the first real judge call.

The repo is its own plugin marketplace, so it also installs straight from GitHub without cloning:

```bash
claude plugin marketplace add GhalebDweikat/winnow
claude plugin install winnow@winnow
```

GitHub shorthand clones over SSH by default; set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` if you don't have an SSH key on this machine.

Installing applies everywhere that shares your `~/.claude` config: the CLI, the desktop app, and IDE extensions. New sessions pick the plugin up; running sessions don't. The first session after install syncs the sidecar's environment and starts the resident server, which takes a few seconds once; after that, sessions share the running server and start instantly.

Installed plugins are copied to `~/.claude/plugins/cache/`, not linked, so after pulling changes run `claude plugin update winnow@winnow`. For a hot-reload loop while developing, load the checkout for one session instead:

```bash
claude --plugin-dir ./winnow
```

To scope the plugin to one project rather than your whole account, add `--scope project` to the `marketplace add` command.

## Add your keys

winnow needs one key for the judge and, optionally, one for summaries. Nothing runs until at least the judge key is in place; until then every hook passes results through untouched and, once per session, tells you so.

**1. Get a Jev key.** Jev is in early access. Join the waitlist at [typesafe.ai](https://typesafe.ai), and once you're admitted create a key at [console.typesafe.ai/settings/keys](https://console.typesafe.ai/settings/keys). No key yet? Skip to step 3.

**2. Put the keys where hooks can see them.** A hook runs with the environment of whatever launched Claude Code. A key exported in one terminal is invisible to the desktop app and to IDE sessions. Either of these works everywhere:

- A file at `~/.winnow/env` (on Windows, `%USERPROFILE%\.winnow\env`), one `KEY=VALUE` per line. winnow reads it on every hook call. Keep it private; it is outside the repo.

  ```
  TYPESAFE_API_KEY=ts-...
  ANTHROPIC_API_KEY=sk-ant-...
  ```

- Or the `env` block of `~/.claude/settings.json`, which Claude Code applies to every session and every subprocess it starts:

  ```json
  { "env": { "TYPESAFE_API_KEY": "ts-...", "ANTHROPIC_API_KEY": "sk-ant-..." } }
  ```

A variable already in the environment wins over the file, so a plain shell export still works for CLI use.

**3. No Jev key yet? Use the adapter.** Add `WINNOW_JUDGE=adapter` to the same file. The adapter sends the identical request to Claude Haiku 4.5 through your Anthropic credentials (`ANTHROPIC_API_KEY`, or a profile from the `ant` CLI's `ant auth login`). Its probabilities are not calibrated, but the whole pipeline works, and switching to Jev later is one line. This path bills your Anthropic account; see [cost](#what-leaves-your-machine-and-what-it-costs).

**4. Verify.**

```bash
uv run --project winnow/sidecar winnow doctor
```

It prints which keys were found, where they came from, and whether each backend initializes. Then `winnow demo` (without `--fake`) makes one real judge call and shows the result.

Summaries use the Anthropic credentials. Set `WINNOW_SUMMARY=0` to turn them off; stubs then say "Summary unavailable" and everything else still works.

### Running `winnow` commands

From the clone, every command is `uv run --project winnow/sidecar winnow <command>`. To have `winnow` on your PATH anywhere:

```bash
uv tool install ./winnow/sidecar
winnow doctor
```

Commands: `doctor`, `demo [--fake]`, `stats`, `recall <key> [--start N --end M]`, `replay {extract,judge,score,run,sample,label,import-labels,agreement}`, `serve [--ensure|--status|--stop]`, `bench [--http]`, `clean`, `mcp`, `hook <event>`.

## What leaves your machine, and what it costs

winnow's job is reading everything Claude reads, so be clear about where it goes.

| Data | Sent to | When |
|---|---|---|
| The tool output being judged, in blocks, plus a short task description from the transcript (last user request, last assistant sentence) and the tool's arguments | TypeSafe (judge `typesafe`) or Anthropic (judge `adapter`) | every judged result over `WINNOW_MIN_CHARS` |
| The hidden blocks only | Anthropic | when summaries are on and something was hidden |
| Your prompt and the first 600 characters of each candidate memory file | the judge | every prompt, when candidate files exist |

Nothing is sent when the judge is `off`, and nothing is sent for outputs under the size threshold. The full text of every hidden output is kept locally in `~/.winnow/cache/` for recall; there is no eviction yet, so clear it when you like.

Approximate cost per judged result, for a 10,000-token output:

| Judge | Judge call | Summaries (up to 4, Haiku 4.5) | Total |
|---|---|---|---|
| Jev at $0.042 per million input tokens | $0.0004 | about $0.005 | under a cent |
| Adapter on Haiku 4.5 at $1 per million input tokens | about $0.01 | about $0.005 | a few cents |

`winnow stats` reports the judge's actual token usage and cost after the fact.

## Configuration

All settings are environment variables (or lines in `~/.winnow/env`). Defaults are deliberately conservative.

| Variable | Default | Meaning |
|---|---|---|
| `WINNOW_MODE` | `active` | `active` rewrites tool results; `shadow` judges and logs only |
| `WINNOW_JUDGE` | `typesafe` | `typesafe`, `adapter`, or `off` |
| `WINNOW_MODEL` | `jev-latest` | Jev model id |
| `WINNOW_JUDGE_TIMEOUT` | `15` | Seconds per judge call, including one retry |
| `WINNOW_ADAPTER_PROVIDER` | `anthropic` | Provider behind the adapter (`anthropic` or `openai`) |
| `WINNOW_ADAPTER_MODEL` | `claude-haiku-4-5` | Model behind the adapter |
| `WINNOW_TOOLS` | `Read,Bash,Grep` | Tools whose output is judged. This can only narrow the set; the hook itself fires for `Read|Bash|Grep` as written in `hooks/hooks.json`, so to add a tool edit that matcher too |
| `WINNOW_QUESTIONS` | `structured` | Question set the judge is asked with: `structured`, `default`, or `strict` |
| `WINNOW_EXCLUDE_PATHS` | `WINNOW_HOME` | Reads under these directories are never judged (path-separator delimited) |
| `WINNOW_EXCLUDE_COMMANDS` | `\bwinnow\b` | Bash commands matching this regex are never judged |
| `WINNOW_PORT` | `47311` | Sidecar port; the URLs in `hooks/hooks.json` must match |
| `WINNOW_MIN_CHARS` | `1500` | Outputs shorter than this are never touched |
| `WINNOW_DROP` | `0.1` | Hide a block only when P(needed) is below this |
| `WINNOW_KEEP` | `0.5` | Error-gate threshold; also the line between "confident keep" and "uncertain keep" |
| `WINNOW_MIN_PRUNE_RATIO` | `0.2` | Skip the rewrite unless at least this fraction of the text would be hidden |
| `WINNOW_BLOCK_LINES` | `25` | Target lines per block |
| `WINNOW_MAX_BLOCKS` | `200` | Cap on questions per call; block size grows to fit |
| `WINNOW_MAX_STATE_CHARS` | `120000` | Blocks beyond this budget are kept unjudged |
| `WINNOW_SUMMARY` | `1` | Summarize hidden groups |
| `WINNOW_SUMMARY_MODEL` | `claude-haiku-4-5` | Summarizer model |
| `WINNOW_SUMMARY_MAX_GROUPS` | `4` | Summarize at most this many hidden groups per result; the rest get "Summary unavailable" |
| `WINNOW_SUMMARY_MAX_CHARS` | `20000` | Characters of a hidden group sent to the summarizer |
| `WINNOW_CONTEXT_DIRS` | | Extra directories of `.md` files for the prompt-time selector. Path-separator delimited: `:` on macOS and Linux, `;` on Windows |
| `WINNOW_CONTEXT_GATE` | `0.5` | Minimum P(relevant) to inject a file |
| `WINNOW_CONTEXT_TOP_K` | `3` | Max files injected per prompt |
| `WINNOW_CONTEXT_MAX_CHARS` | `8000` | Total injected characters (Claude Code caps hook output at 10,000) |
| `WINNOW_CONTEXT_MAX_CANDIDATES` | `60` | Max files considered per prompt |
| `WINNOW_HOME` | `~/.winnow` | Cache, decision log, env file |

## Shadow mode: run it for a week without trusting it

```
WINNOW_MODE=shadow
```

In shadow mode every hook does its full job, judging, caching and logging, but never changes what Claude sees and never calls the summarizer. Use it for the first week with any judge. `winnow stats` then reports how many results it *would* have rewritten and how many characters it would have saved, and each decision line in `~/.winnow/decisions.jsonl` carries the per-block probabilities and a cache key, so `winnow recall <key>` shows you exactly what would have been hidden. Switch to `WINNOW_MODE=active` when the decisions look right.

## Replay: score a judge on your own history, no key needed

Your Claude Code transcripts already hold hundreds of large tool results, each followed by what Claude did next. `winnow replay` turns that into a labeled benchmark and scores a judge against it, offline.

```bash
winnow replay run --judge lexical            # every transcript under ~/.claude/projects
winnow replay run --judge lexical --limit 200 path/to/session.jsonl
```

The label is weak but free: a block counts as *needed* if, later in the same turn, Claude reused one of its lines in an edit, write or command, or mentioned a distinctive identifier that appears in few other blocks. Blocks that Claude read, understood, and never quoted get labeled *not needed*, so treat the reported regret as an upper bound.

The report gives, for each `WINNOW_DROP` threshold, how much would be hidden and what share of needed blocks that would cost (regret), plus a calibration table and expected calibration error. The `lexical` judge is a keyless word-overlap baseline; any real judge has to beat it. When you have a key:

```bash
winnow replay judge --judge adapter        # re-judge the same cases
winnow replay judge --judge typesafe
winnow replay score --judged ~/.winnow/replay/judged-typesafe.jsonl
```

Cases, judged files and scores live in `~/.winnow/replay/`. Nothing leaves the machine unless you pick a judge that calls an API.

### Hand labels

Weak labels are good enough to rank judges and not good enough to trust a regret number. To get real labels, draw a blind sample and label it:

```bash
winnow replay sample --judged ~/.winnow/replay/judged-typesafe.jsonl      # 100 blocks, stratified by judge probability
winnow replay label --sample ~/.winnow/replay/sample.jsonl --labeler you   # interactive: y needed, x not needed, u unsure
winnow replay agreement --sample ~/.winnow/replay/sample.jsonl             # weak vs you, labeler vs labeler
winnow replay score --judged ~/.winnow/replay/judged-typesafe.jsonl --labels ~/.winnow/replay/labels.jsonl --labeler you
```

The sample also comes as a Markdown sheet (`sample.md`) if you would rather read it in an editor and import answers from a text file with `winnow replay import-labels`. A second labeler on a subset (`--limit 20 --seed 2`) gives an agreement number.

### Question sets

The words the judge is asked with matter. `WINNOW_QUESTIONS` selects a set, and `winnow replay judge --questions <name>` scores one against the same cases as any other:

| set | what it is | result on hand labels |
|---|---|---|
| `structured` (default) | criteria as `what` / `not_for` / `examples` objects | clean below 0.1, hides ~5% of text there |
| `default` | one-line criteria | clean below 0.1, hides ~2% |
| `strict` | "directly about the task" | overconfident: 23% of its bottom bin was needed |

### Measured

First results on 300 real cases, 97 blind hand labels, three question sets and the sidecar's latency are in [docs/DESIGN.md](docs/DESIGN.md#first-numbers-jev-vs-the-lexical-baseline), with the raw score files under `docs/results/` and a draft write-up in [docs/WRITEUP.md](docs/WRITEUP.md).

## The resident sidecar

The hooks are `http` hooks against `winnow serve` on `127.0.0.1:47311`. The SessionStart hook runs `winnow serve --ensure`, which starts a detached server if none is answering and replaces one left over from an older plugin version. The server keeps the SDK loaded and the judge's connection warm, re-reads `~/.winnow/env` whenever it changes, and exits after 45 idle minutes.

```bash
winnow serve --status   # is it up, how many requests, is the judge built
winnow serve --stop
winnow serve --ensure   # what SessionStart runs; prints nothing
winnow bench --http     # 381 ms via a command hook, 16 ms via the sidecar, on the machine this was built on
```

If the server is down, results pass through unjudged and Claude Code shows the hook error; the next session start brings it back. Set `WINNOW_PORT` and edit the URLs in `hooks/hooks.json` together if the port is taken.

## Function hooks (early access)

Claude Code is replacing shell and http hooks with **function hooks**: a TypeScript module the engine loads in-process, whose `tool.call` handler wraps a tool and can hand back a different result. winnow ships one, [`hooks/winnow.ts`](hooks/winnow.ts), next to the http hooks. Claude Code loads it when it runs with `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` (2.1.260 or newer) and ignores it otherwise; nothing else changes.

What the module does differently:

- The task the judge sees comes from the live session (`$.session.messages()`), not from the transcript file, so it is never stale and a subagent's calls carry the subagent's own task.
- When a result is rewritten you see a toast: `winnow: hid 3 of 8 blocks of Read (5.1k to 1.8k chars; winnow_recall ab12)`.
- Small results never leave the process. Large ones go to the same sidecar, which judges them exactly as before; the module only carries the result there and back.

Both hook paths fire for the same call in a session with the flag on. The sidecar dedupes by tool call id: one judge call, and the same rewrite goes back on both paths (`winnow serve --status` counts them as `deduped`). A prompt reported twice within 30 seconds is answered once.

To turn the flag on everywhere Claude Code runs, add it to `~/.claude/settings.json`:

```json
{ "env": { "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1" } }
```

The module's tests run under Claude Code's own kit, with no key and no sidecar (the kit has no network, so they cover the pass-through path and the task reconstruction):

```bash
CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude plugin test .
```

For editor types, run `/plugin-types ./.claude/types` inside a Claude Code session with the flag on; the `tsconfig.json` at the repo root already includes that folder, `hooks/` and `tests/`. The surface is early access and may change between releases; the http hooks stay as the fallback.

## Housekeeping

```bash
winnow bench          # hook overhead with the judge off
winnow clean          # drop cache entries older than 30 days, then trim to 200 MB
```

## Measuring it

```bash
winnow stats
```

Reports outputs judged and rewritten, characters and estimated tokens saved, judge latency and cost, and the **regret rate**: the share of hidden outputs that Claude later asked to recall.

The better regret number comes from you. `winnow review` walks the stubs from your recent sessions, newest first, and shows the whole picture a person needs: what was asked (for a subagent, its delegation prompt), which subagent ran it, what Claude kept and what it lost, what Claude did in its next few actions, and an automatic check for whether any of those actions reused a line from the hidden text. Then one question per stub: was hiding that fine? Ten of these while the session is still fresh in your head are worth more than a hundred labels on old transcripts, and `winnow stats` reports the result as human regret.

```bash
winnow review --limit 10          # y fine, x should have been kept, u unsure
``` Regret against `WINNOW_DROP` is the calibration curve for your own workload. Every decision, with per-block probabilities, is one line in `~/.winnow/decisions.jsonl`; demo runs are logged but excluded from stats.

Recall from the shell:

```bash
winnow recall a1b2c3d4e5f6 --start 41 --end 188
```

## Troubleshooting

Working and silently disabled look the same from inside a session, so check in this order.

1. **Is the plugin enabled?** `claude plugin list` should show `winnow@winnow` as enabled. Enable with `claude plugin enable winnow@winnow` and start a new session.
2. **Is the sidecar up?** `winnow serve --status`. If not, `winnow serve --ensure` starts it; the SessionStart hook does the same. Claude Code also shows a hook error on every judged call while it is down.
3. **Can the judge start?** `winnow doctor`. The common failure is a missing key, or a key set in a terminal that the desktop app never sees. When the judge can't start, winnow also posts one message per session saying so.
4. **Did it fire?** `tail -1 ~/.winnow/decisions.jsonl` after reading a large file. A line with `"rewritten": true` and a `key` means a stub went to Claude. `"reason": "nothing_to_prune"` means the judge thought every block mattered; `"below_min_prune_ratio"` means it would have hidden less than `WINNOW_MIN_PRUNE_RATIO` of the text, so the rewrite was skipped (the usual outcome on ordinary source files at a conservative `WINNOW_DROP`). No line at all means the hook didn't run: check `~/.winnow/errors.log`, then `claude --debug` and look for hook errors.
5. **Everything passes through with `judge_error`.** Read `~/.winnow/errors.log`; it has the traceback. Timeouts show up as `TypeSafeAPITimeoutError`; raise `WINNOW_JUDGE_TIMEOUT` or lower `WINNOW_MAX_STATE_CHARS`.
6. **Stubs appear but nothing is summarized.** Summaries need Anthropic credentials. `winnow doctor` shows whether they were found.
7. **A file you need came back pruned.** Use the stub's key with `winnow_recall`, or read the range it names with `offset`/`limit`. To keep a directory out of winnow's reach entirely, add it to `WINNOW_EXCLUDE_PATHS`.

## Uninstall

```bash
claude plugin uninstall winnow@winnow
claude plugin marketplace remove winnow
```

Then delete `~/.winnow` (cache, decision log, and your env file) if you don't want it kept.

## Windows notes

Claude Code runs hooks under Git Bash when it is installed, otherwise PowerShell; winnow's hook commands work in both. Paths from Claude Code arrive with backslashes, which winnow handles. The env file lives at `%USERPROFILE%\.winnow\env`. `WINNOW_CONTEXT_DIRS` uses `;` between directories. `uv` installs with `winget install astral-sh.uv` or from [astral.sh](https://docs.astral.sh/uv/getting-started/installation/).

## Layout

```
winnow/
├── .claude-plugin/plugin.json   plugin manifest
├── .claude-plugin/marketplace.json  makes the repo installable as a marketplace
├── hooks/hooks.json             SessionStart starts the sidecar; PostToolUse + UserPromptSubmit are http hooks to it
├── hooks/winnow.ts              the same two hooks as a function-hook module (in-process, early access)
├── tests/winnow.test.ts         its tests, for `claude plugin test`
├── .mcp.json                    winnow_recall / winnow_stats MCP server
├── skills/winnow/SKILL.md       teaches Claude what a stub means
├── sidecar/                     Python package (uv project)
│   ├── src/winnow/
│   │   ├── hooks.py             the two handlers
│   │   ├── judge.py             Jev / adapter backends, one interface
│   │   ├── transcript.py        derive "current task" from the session transcript
│   │   ├── extract.py           tool_response → text → tool_response
│   │   ├── serve.py             the resident sidecar (both hook paths; dedupes them)
│   │   ├── questions.py         question sets the judge is asked with
│   │   ├── replay.py  labels.py  the offline benchmark and hand-labeling tools
│   │   ├── demo.py  bench.py    winnow demo, winnow bench
│   │   ├── chunk.py  policy.py  stub.py  summarize.py  cache.py  log.py  memory.py  config.py
│   │   ├── mcp_server.py        recall server
│   │   └── cli.py               all commands
│   └── tests/
└── docs/DESIGN.md               decisions, limits, roadmap
```

## Development

```bash
cd winnow/sidecar
uv sync
uv run pytest
```

Tests run with the judge off and a fake judge, so they need no keys and no network.

## Roadmap

See [docs/DESIGN.md](docs/DESIGN.md). In short: read-narrowing on `PreToolUse`, a done-ness gate on `Stop`, a resident sidecar for lower latency, cache eviction, and a published regret-versus-threshold curve on real sessions.

## License

MIT
