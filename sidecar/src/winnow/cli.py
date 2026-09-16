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
        from winnow.hooks import Runtime, post_tool_use, user_prompt_submit

        payload = _read_stdin_json()
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

    args = parser.parse_args(argv)
    loaded = load_env_file()
    if args.command == "demo":
        from winnow.demo import run_demo

        return run_demo(fake=args.fake)
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
