import json

from winnow import replay
from winnow.chunk import chunk
from winnow.config import Config


def big_file() -> str:
    lines = [f"# license line {i} of a long boilerplate header that says nothing" for i in range(1, 26)]
    lines += [f"import module_{i}" for i in range(1, 26)]
    lines += ["def compute_invoice_total(items):", "    subtotal = sum(i.price for i in items)", "    return apply_discount_rules(subtotal)"]
    lines += [f"    # padding {i}" for i in range(1, 23)]
    lines += [f"FLAG_{i} = {i % 2 == 0}" for i in range(1, 26)]
    lines += [f"def unrelated_helper_{i}():\n    return {i}" for i in range(1, 14)]
    return "\n".join(lines)


def transcript(tmp_path, text: str, *, evidence: bool = True):
    entries = [
        {"type": "user", "message": {"role": "user", "content": "fix the invoice total rounding bug"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "I'll read billing.py first."}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "billing.py"}}]}},
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}]},
            "toolUseResult": {"type": "text", "file": {"filePath": "billing.py", "content": text, "numLines": 120, "startLine": 1, "totalLines": 120}},
        },
    ]
    if evidence:
        entries += [
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "The bug is in compute_invoice_total; FLAG_7 is unrelated."}]}},
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "billing.py", "old_string": "    subtotal = sum(i.price for i in items)", "new_string": "    subtotal = round(sum(i.price for i in items), 2)"}}]},
            },
        ]
    entries += [
        {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "subagent noise"}},
        {"type": "user", "message": {"role": "user", "content": "thanks, now the next thing"}},
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def test_extracts_case_with_task_and_evidence(tmp_path, cfg):
    path = transcript(tmp_path, big_file())
    cases = list(replay.iter_cases(path, tools=cfg.tools, min_chars=cfg.min_chars))
    assert len(cases) == 1
    case = cases[0]
    assert case.tool_name == "Read" and case.tool_input == {"file_path": "billing.py"}
    assert case.task["user_request"] == "fix the invoice total rounding bug"
    assert case.task["assistant_intent"] == "I'll read billing.py first."
    assert any("compute_invoice_total" in e for e in case.evidence)
    assert any("round(sum" in e for e in case.evidence)
    assert not any("subagent noise" in e for e in case.evidence)


def test_weak_labels_mark_quoted_and_mentioned_blocks_as_needed(tmp_path, cfg):
    case = next(replay.iter_cases(transcript(tmp_path, big_file()), tools=cfg.tools, min_chars=cfg.min_chars))
    blocks = chunk(case.text, block_lines=25)
    labels = replay.label_blocks(case, blocks)
    by_content = {b.id: b.text for b in blocks}
    edited = next(bid for bid, t in by_content.items() if "subtotal = sum" in t)
    flagged = next(bid for bid, t in by_content.items() if "FLAG_7 " in t)
    license_block = next(bid for bid, t in by_content.items() if "license line 1 " in t)
    assert labels[edited] == "needed"  # exact line reused in an Edit
    assert labels[flagged] == "needed"  # distinctive identifier mentioned in prose
    assert labels[license_block] == "not_needed"


def test_evidence_window_bounds_what_counts(tmp_path, cfg):
    path = transcript(tmp_path, big_file())
    # window=1: only the first assistant event after the result (the prose mentioning compute_invoice_total)
    case = next(replay.iter_cases(path, tools=cfg.tools, min_chars=cfg.min_chars, window=1))
    assert case.evidence_events == 1
    assert not any("round(sum" in e for e in case.evidence)  # the Edit fell outside the window
    labeled = replay.label_blocks_with_reasons(case, chunk(case.text, block_lines=25))
    reasons = {reason for _, reason in labeled.values()}
    assert "ident" in reasons and "line" not in reasons


def test_only_references_count_as_identifier_mentions(tmp_path, cfg):
    case = next(replay.iter_cases(transcript(tmp_path, big_file()), tools=cfg.tools, min_chars=cfg.min_chars))
    blocks = chunk(case.text, block_lines=25)
    base = replay.case_to_dict(case)
    # Evidence that only overlaps by name (a Write, say) never fires the identifier rule.
    no_refs = replay.Case(**{**base, "evidence": ["unrelated prose about FLAG_7"], "references": [], "evidence_events": 1})
    assert set(replay.label_blocks_with_reasons(no_refs, blocks).values()) == {("not_needed", "none")}
    # Prose that names a distinctive identifier does.
    prose = replay.Case(**{**base, "evidence": [], "references": ["see FLAG_7 and compute_invoice_total"], "evidence_events": 1})
    labeled = replay.label_blocks_with_reasons(prose, blocks)
    assert ("needed", "ident") in labeled.values() and ("needed", "line") not in labeled.values()


def test_no_evidence_means_unknown(tmp_path, cfg):
    case = next(replay.iter_cases(transcript(tmp_path, big_file(), evidence=False), tools=cfg.tools, min_chars=cfg.min_chars))
    labels = replay.label_blocks(case, chunk(case.text, block_lines=25))
    assert set(labels.values()) == {"unknown"}


def test_run_replay_with_lexical_judge_scores_and_writes_files(tmp_path, cfg):
    path = transcript(tmp_path, big_file())
    scored, score_path = replay.run_replay(cfg, paths=[path], judge_name="lexical", limit=None)
    assert scored["cases"] == 1 and scored["cases_with_errors"] == 0
    assert scored["blocks_scored"] >= 4
    assert len(scored["thresholds"]) == 9 and len(scored["calibration"]) == 10
    assert 0 <= scored["ece"] <= 1
    assert score_path.exists() and (cfg.replay_dir / "cases.jsonl").exists() and (cfg.replay_dir / "judged-lexical.jsonl").exists()
    report = replay.format_report(scored)
    assert "threshold" in report and "regret" in report


def test_run_replay_with_fake_judge_computes_regret(tmp_path, cfg, fake_judge_cls):
    path = transcript(tmp_path, big_file())
    case = next(replay.iter_cases(path, tools=cfg.tools, min_chars=cfg.min_chars))
    blocks = chunk(case.text, block_lines=cfg.block_lines, max_blocks=cfg.max_blocks)
    labels = replay.label_blocks(case, blocks)
    needed = [b.id for b in blocks if labels[b.id] == "needed"]
    # a judge that hides exactly the needed blocks has regret 1.0 at any threshold above its probability
    judge = fake_judge_cls({bid: 0.05 for bid in needed}, default=0.95)
    scored, _ = replay.run_replay(cfg, paths=[path], judge_name="fake", limit=None, judge=judge)
    at_half = next(r for r in scored["thresholds"] if r["threshold"] == 0.5)
    assert at_half["regret"] == 1.0 and at_half["hidden_blocks"] == len(needed)
    at_low = next(r for r in scored["thresholds"] if r["threshold"] == 0.1)
    assert at_low["regret"] == 1.0  # 0.05 < 0.1 still hides them


def test_case_round_trips_through_jsonl(tmp_path, cfg):
    case = next(replay.iter_cases(transcript(tmp_path, big_file()), tools=cfg.tools, min_chars=cfg.min_chars))
    out = tmp_path / "cases.jsonl"
    replay.write_jsonl(out, [replay.case_to_dict(case)])
    back = replay.case_from_dict(next(replay.read_jsonl(out)))
    assert back == case
