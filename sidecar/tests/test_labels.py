import json

from winnow import labels as L
from winnow import replay
from winnow.chunk import chunk


def make_files(tmp_path, cfg):
    text = "\n".join(f"line {i} of a block with enough text to be significant" for i in range(1, 101))
    case = replay.Case(id="c1", transcript="t", tool_use_id="t1", tool_name="Read", tool_input={"file_path": "f.py"}, task={"user_request": "fix it", "assistant_intent": "reading"}, line_offset=1, text=text)
    cases_path = tmp_path / "cases.jsonl"
    replay.write_jsonl(cases_path, [replay.case_to_dict(case)])
    blocks = chunk(text, block_lines=25)
    ps = [0.05, 0.15, 0.35, 0.9]
    judged = {
        "case_id": "c1", "tool": "Read", "task": case.task, "judge": "fake", "input_tokens": 10, "latency_ms": 5,
        "blocks": [{"id": b.id, "start": b.start, "end": b.end, "chars": len(b.text), "label": "needed" if i % 2 else "not_needed", "reason": "line", "p": ps[i]} for i, b in enumerate(blocks)],
    }
    judged_path = tmp_path / "judged.jsonl"
    replay.write_jsonl(judged_path, [judged])
    return cases_path, judged_path


def test_sample_is_stratified_blind_and_numbered(tmp_path, cfg):
    cases_path, judged_path = make_files(tmp_path, cfg)
    items = L.sample(cfg, judged_path, cases_path, n_low=2, n_mid=1, n_high=1, seed=3)
    assert len(items) == 4 and sorted(it["n"] for it in items) == [1, 2, 3, 4]
    assert {it["bin"] for it in items} == {"low", "mid", "high"}
    assert all(it["text"].startswith("line") for it in items)
    out, md = tmp_path / "sample.jsonl", tmp_path / "sample.md"
    L.write_sample(items, out, md)
    sheet = md.read_text(encoding="utf-8")
    assert "## 1" in sheet and "**User asked:** fix it" in sheet
    assert "weak_label" not in sheet and "not_needed" not in sheet  # blind: no labels leak into items
    assert "0.05" not in sheet and "0.15" not in sheet  # nor probabilities


def test_import_answers_and_agreement(tmp_path, cfg):
    cases_path, judged_path = make_files(tmp_path, cfg)
    items = L.sample(cfg, judged_path, cases_path, n_low=2, n_mid=1, n_high=1, seed=3)
    labels_path = tmp_path / "labels.jsonl"
    answers = L.parse_answers("1 y\n2 x\n3: u\n# comment\n4 - needed\nbogus line\n")
    assert answers == {1: "needed", 2: "not_needed", 3: "unsure", 4: "needed"}
    assert L.import_answers(items, answers, labels_path, "alice") == 4
    loaded = L.load_labels(labels_path)
    assert set(loaded) == {"alice"} and len(loaded["alice"]) == 4
    # a second labeler agreeing on two, disagreeing on one
    L.import_answers(items, {1: "needed", 2: "needed", 4: "needed"}, labels_path, "bob")
    result = L.agreement(items, L.load_labels(labels_path))
    assert result["labelers"]["alice"]["n"] == 3 and result["labelers"]["alice"]["unsure"] == 1
    assert result["pairs"]["alice vs bob"] == {"n": 3, "agreement": round(2 / 3, 3)}
    assert set(result["labelers"]["bob"]["hand_needed_rate_by_bin"]) == {"low", "mid", "high"}


def test_score_against_hand_labels_uses_only_labeled_blocks(tmp_path, cfg):
    cases_path, judged_path = make_files(tmp_path, cfg)
    items = L.sample(cfg, judged_path, cases_path, n_low=2, n_mid=1, n_high=1, seed=3)
    labels_path = tmp_path / "labels.jsonl"
    # label every sampled block "not_needed" except the high-p one
    answers = {it["n"]: ("needed" if it["bin"] == "high" else "not_needed") for it in items}
    L.import_answers(items, answers, labels_path, "alice")
    hand = L.hand_label_map(L.load_labels(labels_path), "alice")
    scored = replay.score(replay.read_jsonl(judged_path), hand_labels=hand)
    assert scored["label_source"] == "hand" and scored["blocks_scored"] == 4
    assert scored["needed_fraction"] == 0.25
    at_half = next(r for r in scored["thresholds"] if r["threshold"] == 0.5)
    assert at_half["regret"] == 0.0 and at_half["hidden_precision"] == 1.0
    assert "hand labels" in replay.format_report(scored)


def test_interactive_labeling_records_and_skips(tmp_path, cfg):
    cases_path, judged_path = make_files(tmp_path, cfg)
    items = L.sample(cfg, judged_path, cases_path, n_low=2, n_mid=1, n_high=1, seed=3)
    labels_path = tmp_path / "labels.jsonl"
    answers = iter(["y", "zz", "x", "s", "q"])
    printed = []
    n = L.label_interactive(items, labels_path, "me", input_fn=lambda _: next(answers), print_fn=printed.append)
    assert n == 2
    saved = [json.loads(l) for l in labels_path.read_text(encoding="utf-8").splitlines()]
    assert [s["label"] for s in saved] == ["needed", "not_needed"]
    assert any("y, x, u, s, or q" in p for p in printed)
    # re-running skips the two already labeled
    n2 = L.label_interactive(items, labels_path, "me", input_fn=lambda _: "q", print_fn=printed.append)
    assert n2 == 0 and any(p.startswith("2 blocks to label") for p in printed)
