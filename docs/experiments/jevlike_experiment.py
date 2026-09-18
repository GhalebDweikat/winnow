"""Train jevlike on winnow's replay cases as a keyless local judge and score it with winnow's harness.

Rows: context = task + tool + block text; options = ("needed", "not needed"); label from the weak label.
Split by transcript so a session never leaks across splits. The held-out split is scored with
winnow.replay.score, and Jev's and the lexical judge's records are rescored on the same cases.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCR = Path(__file__).resolve().parent
sys.path.insert(0, r"C:\GenAI\Claude\Breakthrough\winnow\sidecar\src")
os.environ.setdefault("WINNOW_JUDGE", "off")

from winnow.chunk import chunk  # noqa: E402
from winnow.config import Config  # noqa: E402
from winnow.replay import case_from_dict, format_report, label_blocks_with_reasons, read_jsonl, score  # noqa: E402

REPLAY = Path.home() / ".winnow" / "replay"
DATA = SCR / "jevdata"
RUNS = SCR / "jevruns"
OPTIONS = ("needed", "not needed")
CONTEXT_TOKENS = int(os.environ.get("JEV_CONTEXT_TOKENS", "2048"))
EPOCHS = int(os.environ.get("JEV_EPOCHS", "8"))
ENCODER = os.environ.get("JEV_ENCODER", "tiny")  # tiny (bytes, from scratch) or hf (frozen pretrained encoder)
HF_MODEL = os.environ.get("JEV_HF_MODEL", "Qwen/Qwen2.5-0.5B")
DEVICE = os.environ.get("JEV_DEVICE", "cpu")
TAG = os.environ.get("JEV_TAG", ENCODER)


def assign_splits(block_counts: dict[str, int]) -> dict[str, str]:
    """Whole transcripts go to one split each; fill 70/15/15 by block count, largest transcripts first."""
    total = sum(block_counts.values()) or 1
    target = {"train": 0.70, "validation": 0.15, "test": 0.15}
    filled = {s: 0 for s in target}
    out: dict[str, str] = {}
    order = sorted(block_counts, key=lambda t: (-block_counts[t], hashlib.sha1(t.encode("utf-8")).hexdigest()))
    for t in order:
        s = min(target, key=lambda name: filled[name] / total - target[name])
        out[t] = s
        filled[s] += block_counts[t]
    return out


def context_for(case, block_text: str) -> str:
    task = case.task if isinstance(case.task, dict) else {}
    return (
        f"TASK: {task.get('user_request', '')}\n"
        f"INTENT: {task.get('assistant_intent', '')}\n"
        f"TOOL: {case.tool_name}\n\n{block_text}"
    )


def build() -> dict[str, list[dict]]:
    cfg = Config.from_env()
    rows: dict[str, list[dict]] = {"train": [], "validation": [], "test": []}
    cases_by_split: dict[str, set[str]] = {"train": set(), "validation": set(), "test": set()}
    kept = skipped = 0
    prepared = []
    counts: dict[str, int] = {}
    for raw in read_jsonl(REPLAY / "cases.jsonl"):
        case = case_from_dict(raw)
        blocks = chunk(case.text, block_lines=cfg.block_lines, max_blocks=cfg.max_blocks)
        labeled = label_blocks_with_reasons(case, blocks)
        prepared.append((case, blocks, labeled))
        counts[case.transcript] = counts.get(case.transcript, 0) + sum(1 for b in blocks if labeled[b.id][0] in ("needed", "not_needed"))
    splits = assign_splits(counts)
    print(f"{len(counts)} transcripts; blocks per transcript: {sorted(counts.values(), reverse=True)}")
    for case, blocks, labeled in prepared:
        split = splits[case.transcript]
        cases_by_split[split].add(case.id)
        for b in blocks:
            label, _reason = labeled[b.id]
            if label not in ("needed", "not_needed"):
                skipped += 1
                continue
            kept += 1
            rows[split].append(
                {
                    "context": context_for(case, b.text),
                    "options": list(OPTIONS),
                    "label": 0 if label == "needed" else 1,
                    "case_id": case.id,
                    "block_id": b.id,
                    "chars": len(b.text),
                    "weak": label,
                    "tool": case.tool_name,
                    "transcript": case.transcript,
                }
            )
    DATA.mkdir(exist_ok=True)
    for split, items in rows.items():
        with open(DATA / f"{split}.jsonl", "w", encoding="utf-8") as f:
            for r in items:
                f.write(json.dumps({"context": r["context"], "options": r["options"], "label": r["label"]}) + "\n")
        with open(DATA / f"{split}.meta.jsonl", "w", encoding="utf-8") as f:
            for r in items:
                f.write(json.dumps({k: v for k, v in r.items() if k != "context"}) + "\n")
    print(
        f"rows kept {kept}, unlabeled skipped {skipped}; "
        + ", ".join(f"{s}: {len(v)} blocks / {len(cases_by_split[s])} cases" for s, v in rows.items())
    )
    for split, items in rows.items():
        n = len(items) or 1
        print(f"  {split}: needed fraction {sum(1 for r in items if r['label'] == 0) / n:.3f}")
    return rows


def train() -> Path:
    RUNS.mkdir(exist_ok=True)
    out = RUNS / f"winnow-weak-{TAG}.pt"
    cmd = [
        sys.executable, "-m", "jevlike.train", str(DATA / "train.jsonl"),
        "--validation", str(DATA / "validation.jsonl"), "--output", str(out),
        "--context-tokens", str(CONTEXT_TOKENS), "--option-tokens", "16",
        "--epochs", str(EPOCHS), "--device", DEVICE, "--encoder", ENCODER,
        "--learning-rate", os.environ.get("JEV_LR", "2e-3"),
    ]
    if ENCODER == "hf":
        cmd += ["--hf-model", HF_MODEL, "--rank", os.environ.get("JEV_RANK", "256"), "--batch-size", os.environ.get("JEV_BATCH", "8")]
    else:
        cmd += ["--batch-size", "32"]
    started = time.time()
    subprocess.run(cmd, check=True)
    print(f"trained in {time.time() - started:.0f}s -> {out}")
    return out


def predict(checkpoint: Path, rows: list[dict]) -> list[float]:
    import torch
    from jevlike.data import ChoiceExample
    from jevlike.model import load_checkpoint, select_device
    from jevlike.train import move

    device = select_device(DEVICE)
    model, collator, _ = load_checkpoint(str(checkpoint), device)
    model.eval()
    out: list[float] = []
    started = time.time()
    with torch.no_grad():
        step = 8 if ENCODER == "hf" else 32
        for i in range(0, len(rows), step):
            batch_rows = rows[i : i + step]
            batch = move(collator([ChoiceExample(r["context"], OPTIONS, 0) for r in batch_rows]), device)
            probs = model(batch).softmax(-1)[:, 0].cpu().tolist()  # P(needed)
            out.extend(float(p) for p in probs)
    ms = (time.time() - started) * 1000 / max(1, len(rows))
    print(f"predicted {len(rows)} blocks at {ms:.1f} ms per block on CPU")
    return out


def records_from(rows: list[dict], probs: list[float]) -> list[dict]:
    by_case: dict[str, dict] = {}
    for r, p in zip(rows, probs):
        rec = by_case.setdefault(
            r["case_id"],
            {"case_id": r["case_id"], "transcript": r["transcript"], "tool": r["tool"], "judge": "jevlike", "questions": "n/a", "latency_ms": 0, "input_tokens": 0, "blocks": []},
        )
        rec["blocks"].append({"id": r["block_id"], "chars": r["chars"], "label": r["weak"], "reason": "weak", "p": p})
    return list(by_case.values())


def restrict(path: Path, case_ids: set[str]) -> list[dict]:
    return [rec for rec in read_jsonl(path) if rec.get("case_id") in case_ids]


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    if stage in ("build", "all"):
        rows = build()
    else:
        rows = {s: [json.loads(l) for l in open(DATA / f"{s}.meta.jsonl", encoding="utf-8")] for s in ("train", "validation", "test")}
        for s in rows:
            for r, line in zip(rows[s], open(DATA / f"{s}.jsonl", encoding="utf-8")):
                r["context"] = json.loads(line)["context"]
    checkpoint = RUNS / f"winnow-weak-{TAG}.pt"
    if stage in ("train", "all"):
        checkpoint = train()
    if stage in ("score", "all"):
        test = rows["test"]
        probs = predict(checkpoint, test)
        case_ids = {r["case_id"] for r in test}
        results = {
            f"jevlike {TAG} (weak-trained, held-out sessions)": records_from(test, probs),
            "jev structured, same cases": restrict(REPLAY / "judged-typesafe-structured.jsonl", case_ids),
            "jev default, same cases": restrict(REPLAY / "judged-typesafe.jsonl", case_ids),
            "lexical, same cases": restrict(REPLAY / "judged-lexical.jsonl", case_ids),
        }
        summary = {}
        for name, recs in results.items():
            scored = score(recs)
            summary[name] = scored
            print(f"\n===== {name} =====")
            print(format_report(scored))
        with open(RUNS / f"summary-{TAG}.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)


if __name__ == "__main__":
    main()
