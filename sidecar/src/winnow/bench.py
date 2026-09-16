"""``winnow bench``: what does the hook cost before the judge is even called?

Claude Code runs ``uv run -q --project <sidecar> winnow hook ...`` for every
matching tool call. Two paths matter:

- **small result** (under ``WINNOW_MIN_CHARS``, the common case): winnow reads
  the payload, sees there is nothing to judge, and exits without importing any
  SDK. This is uv + interpreter startup + a few light modules.
- **judged result**: on top of that, importing ``typesafe_sdk`` (and its HTTP
  stack) before the request can go out.

Both are measured here with the judge off, so the numbers are pure overhead.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SIDECAR = Path(__file__).resolve().parents[2]

SMALL_PAYLOAD = json.dumps(
    {
        "hook_event_name": "PostToolUse",
        "session_id": "bench",
        "tool_use_id": "bench",
        "tool_name": "Bash",
        "tool_input": {"command": "true"},
        "tool_response": {"stdout": "ok", "stderr": "", "interrupted": False, "isImage": False},
    }
).encode("utf-8")


def _timed(cmd: list[str], runs: int, env: dict[str, str], stdin: bytes | None = None) -> dict[str, Any]:
    times: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        proc = subprocess.run(cmd, input=stdin, capture_output=True, env=env, check=False)
        times.append((time.perf_counter() - started) * 1000)
        if proc.returncode != 0:
            return {"error": proc.stderr.decode("utf-8", errors="replace")[-400:]}
    return {"median_ms": round(statistics.median(times)), "min_ms": round(min(times)), "max_ms": round(max(times))}


def _fmt(label: str, r: dict[str, Any]) -> str:
    if "error" in r:
        return f"  {label:<44} FAILED: {r['error']}"
    return f"  {label:<44} {r['median_ms']:>5} / {r['min_ms']:>5} / {r['max_ms']:>5}"


def run_bench(runs: int = 10, skip_uv: bool = False) -> int:
    env = {**os.environ, "WINNOW_JUDGE": "off", "WINNOW_SUMMARY": "0"}
    py = sys.executable
    uv = None if skip_uv else shutil.which("uv")

    bare = _timed([py, "-c", "pass"], runs, env)
    fast_py = _timed([py, "-m", "winnow", "hook", "post-tool-use"], runs, env, SMALL_PAYLOAD)
    sdk = _timed([py, "-c", "import typesafe_sdk"], runs, env)
    fast_uv = _timed([uv, "run", "-q", "--project", str(SIDECAR), "winnow", "hook", "post-tool-use"], runs, env, SMALL_PAYLOAD) if uv else None

    print(f"hook overhead, judge off, {runs} runs each (median / min / max, ms)")
    print(_fmt("interpreter only", bare))
    print(_fmt("small result via python -m winnow", fast_py))
    if fast_uv is not None:
        print(_fmt("small result via uv run (what Claude Code runs)", fast_uv))
    print(_fmt("interpreter + import typesafe_sdk", sdk))

    if all("error" not in r for r in (bare, fast_py, sdk)):
        sdk_cost = sdk["median_ms"] - bare["median_ms"]
        base = fast_uv["median_ms"] if fast_uv and "error" not in fast_uv else fast_py["median_ms"]
        print()
        print(f"small results (under WINNOW_MIN_CHARS) pay about {base} ms.")
        print(f"judged results pay about {base + sdk_cost} ms before the judge request goes out ({sdk_cost} ms is the SDK import).")
        print("A resident sidecar (http hook) would cut both to a local round trip.")
    return 0
