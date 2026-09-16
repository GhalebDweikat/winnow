# winnow

A calibrated context sieve for Claude Code.

Every large `Read`, `Bash`, or `Grep` result is judged by a System One model before it enters context. Blocks the judge is confident you don't need are replaced with a three-line stub: what was hidden, a one-paragraph summary from a cheap model, and a key that restores the full text on demand. Nothing is ever lost; it just stops costing tokens until you ask for it.

The judge is [TypeSafe's Jev](https://typesafe.ai), a model that answers typed yes/no questions with calibrated probabilities instead of generating text. One call, one question per block, all evaluated in parallel, a few hundred milliseconds. Until you have a Jev key, the same code runs against TypeSafe's LLM-backed adapter.

## What it does

```
Read big.py  ──►  Claude Code  ──►  PostToolUse hook  ──►  winnow
                                                             │
        split into blocks ◄──────────────────────────────────┘
        ask the judge: "is block N needed for the current task?"  (one call, N questions)
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

Two safety rules are hard-coded. If the judge thinks the output shows an error, nothing is hidden. If a block's probability is merely uncertain (between `drop` and `keep`), it is kept.

A second hook runs at prompt time. It ranks your project's memory files (and any directories you point it at) against the prompt and injects the relevant ones, so Claude reads what it needs without a round of `Read` calls.

## Install

Requirements: Python 3.10+, [uv](https://docs.astral.sh/uv/), Claude Code 2.1.121 or newer (the version that let hooks replace tool output for all tools).

The repo is its own plugin marketplace, so it installs like any other Claude Code plugin. Installing applies to every Claude Code surface that shares your `~/.claude` config: the CLI, the desktop app, and IDE extensions.

**From GitHub** (the repo is private for now, so clone over HTTPS with your `gh` credentials):

```bash
CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1 claude plugin marketplace add GhalebDweikat/winnow
claude plugin install winnow@winnow
```

**From a local clone** (what you want while developing):

```bash
git clone https://github.com/GhalebDweikat/winnow.git
claude plugin marketplace add ./winnow
claude plugin install winnow@winnow
```

After pulling changes, run `claude plugin update winnow@winnow`; installed plugins are copied, not linked. For a hot-reload loop instead, load the checkout directly for one session:

```bash
claude --plugin-dir ./winnow
```

To scope the plugin to one project rather than your whole account, add `--scope project` to the `marketplace add` command; that writes it into that project's `.claude/settings.json`.

Turn it off without uninstalling: `claude plugin disable winnow@winnow`.

The first hook invocation runs `uv sync` in `sidecar/`, which takes a few seconds once. Until you set a judge key (below), every hook passes the tool result through untouched and logs the reason in `~/.winnow/errors.log`.

## Add your keys

winnow needs one key for the judge and, optionally, one for summaries. Nothing runs until at least the judge key is in place; until then every hook passes results through untouched.

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

**3. No Jev key yet? Use the adapter.** Add `WINNOW_JUDGE=adapter` to the same file. The adapter sends the identical request to Claude Haiku 4.5 through your Anthropic credentials (`ANTHROPIC_API_KEY`, or an `ant auth login` profile). Its probabilities are not calibrated, but the whole pipeline works, and switching to Jev later is one line.

**4. Verify.**

```bash
uv run --project sidecar winnow doctor
```

It prints which keys were found, where they came from, and whether each backend initializes.

Summaries use the Anthropic credentials. Set `WINNOW_SUMMARY=0` to turn them off; stubs then say "Summary unavailable" and everything else still works.

## Configuration

All settings are environment variables. Defaults are deliberately conservative.

| Variable | Default | Meaning |
|---|---|---|
| `WINNOW_JUDGE` | `typesafe` | `typesafe`, `adapter`, or `off` |
| `WINNOW_MODEL` | `jev-latest` | Jev model id |
| `WINNOW_ADAPTER_MODEL` | `claude-haiku-4-5` | Model behind the adapter |
| `WINNOW_TOOLS` | `Read,Bash,Grep` | Tools whose output is judged |
| `WINNOW_MIN_CHARS` | `1500` | Outputs shorter than this are never touched |
| `WINNOW_DROP` | `0.3` | Hide a block only when P(needed) is below this |
| `WINNOW_KEEP` | `0.5` | Error-gate threshold; also the line between "confident keep" and "uncertain keep" |
| `WINNOW_MIN_PRUNE_RATIO` | `0.2` | Skip the rewrite unless at least this fraction of the text would be hidden |
| `WINNOW_BLOCK_LINES` | `25` | Target lines per block |
| `WINNOW_MAX_BLOCKS` | `200` | Cap on questions per call; block size grows to fit |
| `WINNOW_MAX_STATE_CHARS` | `120000` | Blocks beyond this budget are kept unjudged |
| `WINNOW_SUMMARY` | `1` | Summarize hidden groups |
| `WINNOW_SUMMARY_MODEL` | `claude-haiku-4-5` | Summarizer model |
| `WINNOW_CONTEXT_DIRS` | | Extra directories of `.md` files for the prompt-time selector (`;`-separated on Windows) |
| `WINNOW_CONTEXT_GATE` | `0.5` | Minimum P(relevant) to inject a file |
| `WINNOW_CONTEXT_TOP_K` | `3` | Max files injected per prompt |
| `WINNOW_HOME` | `~/.winnow` | Cache and decision log |

## Measuring it

```bash
uv run --project sidecar winnow stats
```

Reports outputs judged and rewritten, characters and estimated tokens saved, judge latency and cost, and the **regret rate**: the share of hidden outputs that Claude later asked to recall. Regret against `WINNOW_DROP` is the calibration curve for your own workload. Every decision, with per-block probabilities, is in `~/.winnow/decisions.jsonl`.

Recall from the shell:

```bash
uv run --project sidecar winnow recall a1b2c3d4e5f6 --start 41 --end 188
```

## Layout

```
winnow/
├── .claude-plugin/plugin.json   plugin manifest
├── hooks/hooks.json             PostToolUse + UserPromptSubmit → sidecar CLI
├── .mcp.json                    winnow_recall / winnow_stats MCP server
├── skills/winnow/SKILL.md       teaches Claude what a stub means
├── sidecar/                     Python package (uv project)
│   ├── src/winnow/
│   │   ├── hooks.py             the two handlers
│   │   ├── judge.py             Jev / adapter backends, one interface
│   │   ├── transcript.py        derive "current task" from the session transcript
│   │   ├── extract.py           tool_response → text → tool_response
│   │   ├── chunk.py  policy.py  stub.py  summarize.py  cache.py  log.py  memory.py
│   │   ├── mcp_server.py        recall server
│   │   └── cli.py               winnow hook | recall | stats | mcp | doctor
│   └── tests/
└── docs/DESIGN.md               decisions, limits, roadmap
```

## Development

```bash
cd sidecar
uv sync
uv run pytest
```

Tests run with the judge off and a fake judge, so they need no keys and no network.

Exercise a hook by hand:

```bash
echo '{"tool_name":"Bash","session_id":"s","tool_use_id":"t","tool_input":{"command":"ls"},"tool_response":{"stdout":"...","stderr":"","interrupted":false,"isImage":false}}' \
  | WINNOW_JUDGE=adapter uv run --project sidecar winnow hook post-tool-use
```

## Roadmap

See [docs/DESIGN.md](docs/DESIGN.md). In short: read-narrowing on `PreToolUse`, a done-ness gate on `Stop`, a resident sidecar for lower latency, and a published regret-versus-threshold curve on real sessions.

## License

MIT
