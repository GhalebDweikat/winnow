import json

from winnow import cache, log, review
from winnow.hooks import Runtime, post_tool_use
from winnow.stub import digest


def numbered(n: int) -> str:
    return "\n".join(f"line {i}" for i in range(1, n + 1))


def make_stub(cfg, fake_judge_cls, tool_use_id="t1"):
    judge = fake_judge_cls({"b001": 0.9, "b002": 0.0, "b003": 0.0, "b004": 0.9, "error_present": 0.0})
    payload = {
        "session_id": "sess-1", "tool_use_id": tool_use_id, "tool_name": "Bash",
        "tool_input": {"command": "cat big.log"},
        "tool_response": {"stdout": numbered(100), "stderr": "", "interrupted": False, "isImage": False},
    }
    assert post_tool_use(payload, Runtime(cfg, judge, None)) is not None


def test_review_walks_recent_stubs_and_records_verdicts(cfg, fake_judge_cls):
    make_stub(cfg, fake_judge_cls, "t1")
    make_stub(cfg, fake_judge_cls, "t2")
    items = review.candidates(cfg, limit=10, since_days=7)
    assert len(items) == 2
    event, entry = items[0]
    groups = review.hidden_groups(entry)
    assert [(g["start"], g["end"]) for g in groups] == [(26, 75)]
    assert groups[0]["text"].startswith("line 26\n") and groups[0]["text"].endswith("line 75")
    assert entry["task"] == {"user_request": "", "assistant_intent": ""}

    answers = iter(["zz", "y", "x"])
    printed = []
    saved = review.run_review(cfg, limit=10, reviewer="tester", input_fn=lambda _: next(answers), print_fn=printed.append)
    assert saved == 2
    verdicts = [json.loads(l)["verdict"] for l in (cfg.home / "review.jsonl").read_text(encoding="utf-8").splitlines()]
    assert verdicts == ["fine", "should_have_kept"]
    assert any("hidden lines 26-75" in p for p in printed)
    stats = log.stats(cfg)
    assert stats["human_reviewed"] == 2 and stats["human_should_have_kept"] == 1 and stats["human_regret_rate"] == 0.5
    # already reviewed stubs are not offered again
    assert review.candidates(cfg, limit=10, since_days=7) == []


def test_review_with_nothing_to_do(cfg):
    printed = []
    assert review.run_review(cfg, print_fn=printed.append, input_fn=lambda _: "q") == 0
    assert "No unreviewed stubs" in printed[0]


def test_grep_digest_names_files_and_counts():
    text = "\n".join(["src/a.py:10:version = 1", "src/a.py:20:version = 2", "lock/uv.lock:5:version = 3"])
    d = digest("Grep", text)
    assert d.startswith("3 matching lines in 2 files") and "src/a.py (2)" in d and "lock/uv.lock (1)" in d


def test_generic_digest_flags_shape_of_content():
    assert "mostly comments" in digest("Read", "\n".join(f"# license line {i}" for i in range(20)))
    assert "highly repetitive" in digest("Bash", "\n".join(["ok"] * 30))
    assert digest("Bash", "") == "blank lines"
    d = digest("Read", "def compute_total(items):\n    return sum(items)")
    assert d.startswith("2 lines, starting: def compute_total")


def test_stub_uses_digest_when_no_summary(cfg, fake_judge_cls):
    make_stub(cfg, fake_judge_cls)
    event, entry = review.candidates(cfg, limit=1, since_days=7)[0]
    assert cache.load(cfg, event["key"]) is not None
    # the live stub text is what Claude saw; rebuild it from the log entry's key via a fresh post_tool_use
    judge = fake_judge_cls({"b001": 0.9, "b002": 0.0, "b003": 0.0, "b004": 0.9, "error_present": 0.0})
    payload = {
        "session_id": "s2", "tool_use_id": "t9", "tool_name": "Grep",
        "tool_input": {"pattern": "version"},
        "tool_response": {"mode": "content", "content": "\n".join(f"uv.lock:{i}:version = \"{i}\"" for i in range(1, 101)), "numLines": 100},
    }
    out = post_tool_use(payload, Runtime(cfg, judge, None))
    content = out["hookSpecificOutput"]["updatedToolOutput"]["content"]
    assert "[winnow] Hidden content: 50 matching lines in 1 file: uv.lock (50)" in content
