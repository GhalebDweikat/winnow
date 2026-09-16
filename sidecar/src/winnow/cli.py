"""Command-line entry point.

``winnow hook <event>`` is what Claude Code runs. It reads the hook payload from
stdin, prints JSON to stdout when it has something to say, and always exits 0.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from winnow import __version__, cache, log
from winnow.config import Config, credential_status, env_file_path, load_env_file


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    payload = json.loads(raw) if raw.strip() else {}
    return payload if isinstance(payload, dict) else {}


def _write_json(obj: Any) -> None:
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def _notify_once(cfg: Config, session_id: str, message: str) -> dict[str, Any] | None:
    """Return a systemMessage the first time per session; stay silent afterwards."""
    marker = cfg.home / "notified" / (session_id or "no-session")
    try:
        if marker.exists():
            return None
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        return None
    return {"systemMessage": message}


def run_hook(event: str) -> int:
    cfg = Config.from_env()
    try:
        from winnow.hooks import Runtime, post_tool_use, user_prompt_submit, worth_judging

        payload = _read_stdin_json()
        if event == "post-tool-use" and not worth_judging(payload, cfg):
            return 0  # fast path: most tool results are small; no judge, no SDK import
        try:
            runtime = Runtime.from_config(cfg)
        except Exception as exc:  # noqa: BLE001 - most often: no key for the configured judge
            log.log_error(cfg, "runtime", exc)
            notice = _notify_once(
                cfg,
                str(payload.get("session_id") or ""),
                f"winnow is installed but its judge could not start ({type(exc).__name__}: {str(exc)[:140]}). "
                "Tool results are passing through untouched. Run `winnow doctor` to fix it.",
            )
            if notice is not None:
                _write_json(notice)
            return 0
        if event == "post-tool-use":
            output = post_tool_use(payload, runtime)
        elif event == "user-prompt-submit":
            output = user_prompt_submit(payload, runtime)
        else:
            output = None
        if output is not None:
            _write_json(output)
    except Exception as exc:  # noqa: BLE001 - a hook must never break the tool call
        log.log_error(cfg, f"hook:{event}", exc)
    return 0


def run_recall(key: str, start: int | None, end: int | None) -> int:
    cfg = Config.from_env()
    entry = cache.load(cfg, key)
    if entry is None:
        print(f"no cached output for key {key!r}", file=sys.stderr)
        return 1
    log.log_event(cfg, {"event": "recall", "key": key, "start": start, "end": end, "via": "cli"})
    print(cache.slice_lines(str(entry.get("text", "")), start, end, int(entry.get("line_offset") or 1)))
    return 0


def run_stats() -> int:
    for name, value in log.stats(Config.from_env()).items():
        print(f"{name:24} {value}")
    return 0


def run_doctor(loaded_from_env_file: list[str]) -> int:
    cfg = Config.from_env()
    print(f"winnow {__version__}")
    print(f"home                     {cfg.home}")
    print(f"mode                     {cfg.mode}" + ("  (judging and logging only; tool results are never changed)" if cfg.shadow else ""))
    env_path = env_file_path()
    print(f"env file                 {env_path} ({'found, loaded ' + ', '.join(loaded_from_env_file) if loaded_from_env_file else ('found, nothing new' if env_path.is_file() else 'not present')})")
    for name, status in credential_status().items():
        print(f"{name:24} {status}")
    print(f"judge                    {cfg.judge} (model={cfg.model if cfg.judge == 'typesafe' else cfg.adapter_model})")
    print(f"tools                    {', '.join(cfg.tools)}")
    print(f"thresholds               drop<{cfg.drop}  keep>={cfg.keep}  min_prune_ratio={cfg.min_prune_ratio}")
    print(f"summary                  {'on' if cfg.summary else 'off'} ({cfg.summary_model})")
    ok = True
    try:
        from winnow.judge import build_judge

        judge = build_judge(cfg)
        print(f"judge backend            {'ok: ' + judge.name if judge else 'off'}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"judge backend            FAILED: {exc!r}")
    try:
        from winnow.summarize import build_summarizer

        summarizer = build_summarizer(cfg)
        print(f"summarizer               {'ok: ' + summarizer.name if summarizer else 'off'}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"summarizer               FAILED: {exc!r}")
    try:
        cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        print(f"cache dir                writable: {cfg.cache_dir}")
    except OSError as exc:
        ok = False
        print(f"cache dir                FAILED: {exc!r}")
    return 0 if ok else 1


def _transcript_paths(raw: list[str]):
    from pathlib import Path

    from winnow.replay import default_transcripts

    if not raw:
        return default_transcripts()
    paths = []
    for item in raw:
        p = Path(item).expanduser()
        if p.is_dir():
            paths.extend(sorted(p.rglob("*.jsonl")))
        elif p.is_file():
            paths.append(p)
        else:
            print(f"winnow replay: not found: {p}", file=sys.stderr)
    return paths


def run_replay_command(args: Any) -> int:
    from pathlib import Path

    from winnow import replay

    cfg = Config.from_env()
    if args.replay_command == "extract":
        paths = _transcript_paths(args.paths)
        out = Path(args.out) if args.out else cfg.replay_dir / "cases.jsonl"
        n = replay.write_jsonl(out, (replay.case_to_dict(c) for c in replay.extract_cases(paths, cfg, limit=args.limit, window=args.window)))
        print(f"{n} cases from {len(paths)} transcripts -> {out}")
        return 0
    if args.replay_command == "judge":
        cases_path = Path(args.cases) if args.cases else cfg.replay_dir / "cases.jsonl"
        if not cases_path.exists():
            print(f"winnow replay: no cases file at {cases_path}; run `winnow replay extract` first", file=sys.stderr)
            return 1
        try:
            judge = replay.build_replay_judge(args.judge, cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"winnow replay: the {args.judge} judge could not start: {exc}", file=sys.stderr)
            return 1
        out = Path(args.out) if args.out else cfg.replay_dir / f"judged-{args.judge}.jsonl"
        cases = (replay.case_from_dict(d) for d in replay.read_jsonl(cases_path))
        n = replay.write_jsonl(out, replay.judge_cases(cases, judge, cfg, limit=args.limit))
        print(f"{n} cases judged by {judge.name} -> {out}")
        print(replay.format_report(replay.score(replay.read_jsonl(out))))
        return 0
    if args.replay_command == "score":
        scored = replay.score(replay.read_jsonl(Path(args.judged)))
        print(replay.format_report(scored))
        if args.json:
            Path(args.json).write_text(json.dumps(scored, indent=2), encoding="utf-8")
        return 0
    if args.replay_command == "run":
        paths = _transcript_paths(args.paths)
        if not paths:
            print("winnow replay: no transcripts found", file=sys.stderr)
            return 1
        try:
            scored, score_path = replay.run_replay(cfg, paths=paths, judge_name=args.judge, limit=args.limit, window=args.window)
        except Exception as exc:  # noqa: BLE001
            print(f"winnow replay: failed: {exc}", file=sys.stderr)
            return 1
        print(replay.format_report(scored))
        print(f"\nscore written to {score_path}")
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="winnow", description="A calibrated context sieve for Claude Code.")
    parser.add_argument("--version", action="version", version=f"winnow {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    hook = sub.add_parser("hook", help="run as a Claude Code hook (payload on stdin)")
    hook.add_argument("event", choices=["post-tool-use", "user-prompt-submit"])

    recall = sub.add_parser("recall", help="print the cached text behind a stub key")
    recall.add_argument("key")
    recall.add_argument("--start", type=int)
    recall.add_argument("--end", type=int)

    sub.add_parser("stats", help="tokens saved, rewrites, regret rate")
    sub.add_parser("mcp", help="run the recall MCP server on stdio")
    sub.add_parser("doctor", help="check configuration and backends")

    demo = sub.add_parser("demo", help="judge a synthetic tool result and show what Claude would see")
    demo.add_argument("--fake", action="store_true", help="use a keyword judge; needs no keys")

    bench = sub.add_parser("bench", help="measure hook startup overhead with the judge off")
    bench.add_argument("--runs", type=int, default=10)
    bench.add_argument("--skip-uv", action="store_true", help="time only the Python entry point, not uv run")

    clean = sub.add_parser("clean", help="delete old cache entries")
    clean.add_argument("--older-than-days", type=float, default=30)
    clean.add_argument("--max-mb", type=float, default=200)
    clean.add_argument("--dry-run", action="store_true")

    replay = sub.add_parser("replay", help="score a judge against your own Claude Code transcripts")
    replay_sub = replay.add_subparsers(dest="replay_command", required=True)
    rp_extract = replay_sub.add_parser("extract", help="transcripts -> cases.jsonl (offline)")
    rp_extract.add_argument("paths", nargs="*", help="transcript .jsonl files or directories; default: all of ~/.claude/projects")
    rp_extract.add_argument("--out")
    rp_extract.add_argument("--limit", type=int)
    rp_extract.add_argument("--window", type=int, default=12, help="assistant events after a result that count as evidence")
    rp_judge = replay_sub.add_parser("judge", help="cases.jsonl -> judged file (calls the judge)")
    rp_judge.add_argument("--cases")
    rp_judge.add_argument("--judge", default="lexical", choices=["lexical", "typesafe", "adapter"])
    rp_judge.add_argument("--out")
    rp_judge.add_argument("--limit", type=int)
    rp_score = replay_sub.add_parser("score", help="judged file -> report (offline)")
    rp_score.add_argument("--judged", required=True)
    rp_score.add_argument("--json", help="also write the score as JSON here")
    rp_run = replay_sub.add_parser("run", help="extract, judge, and score in one go")
    rp_run.add_argument("paths", nargs="*")
    rp_run.add_argument("--judge", default="lexical", choices=["lexical", "typesafe", "adapter"])
    rp_run.add_argument("--limit", type=int, help="max cases to extract")
    rp_run.add_argument("--window", type=int, default=12, help="assistant events after a result that count as evidence")

    args = parser.parse_args(argv)
    loaded = load_env_file()
    if args.command == "demo":
        from winnow.demo import run_demo

        return run_demo(fake=args.fake)
    if args.command == "bench":
        from winnow.bench import run_bench

        return run_bench(runs=args.runs, skip_uv=args.skip_uv)
    if args.command == "clean":
        result = cache.clean(Config.from_env(), older_than_days=args.older_than_days, max_mb=args.max_mb, dry_run=args.dry_run)
        for name, value in result.items():
            print(f"{name:32} {value}")
        return 0
    if args.command == "replay":
        return run_replay_command(args)
    if args.command == "hook":
        return run_hook(args.event)
    if args.command == "recall":
        return run_recall(args.key, args.start, args.end)
    if args.command == "stats":
        return run_stats()
    if args.command == "mcp":
        from winnow.mcp_server import main as mcp_main

        mcp_main()
        return 0
    if args.command == "doctor":
        return run_doctor(loaded)
    return 2
