import json

from winnow import cache, log, review
from winnow.hooks import Runtime, post_tool_use
from winnow.stub import digest


def numbered(n: int) -> str:
    return "\n".join(f"line {i}" for i in range(1, n + 1))


def make_stub(cfg, fake_judge_cls, tool_use_id="t1", text=None):
    judge = fake_judge_cls({"b001": 0.9, "b002": 0.0, "b003": 0.0, "b004": 0.9, "error_present": 0.0})
    payload = {
        "session_id": "sess-1", "tool_use_id": tool_use_id, "tool_name": "Bash",
        "tool_input": {"command": "cat big.log"},
        "tool_response": {"stdout": text or numbered(100), "stderr": "", "interrupted": False, "isImage": False},
    }
    assert post_tool_use(payload, Runtime(cfg, judge, None)) is not None


def log_lines(n: int) -> str:
    return "\n".join(f"line {i} of the big build log" for i in range(1, n + 1))


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


def test_review_shows_task_kept_regions_and_next_actions_from_the_transcript(cfg, fake_judge_cls, tmp_path, monkeypatch):
    make_stub(cfg, fake_judge_cls, "t1", text=log_lines(100))  # session sess-1, Bash `cat big.log`, hides lines 26-75
    # a transcript for that session: the user asked, Claude read the log, then edited using a hidden line
    root = tmp_path / "projects"
    (root / "C--proj").mkdir(parents=True)
    entries = [
        {"type": "custom-title", "customTitle": "Debug the flaky build"},
        {"type": "user", "message": {"role": "user", "content": "why does the build log say line 30?"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Let me look at the log."}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat big.log"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}]},
         "toolUseResult": {"stdout": log_lines(100), "stderr": "", "interrupted": False, "isImage": False}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Found it on line 30 of the log."}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "build.py", "old_string": "line 30 of the big build log", "new_string": "line 30 fixed"}}]}},
        {"type": "user", "message": {"role": "user", "content": "thanks"}},
    ]
    (root / "C--proj" / "sess-1.jsonl").write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    monkeypatch.setenv("WINNOW_TRANSCRIPTS_ROOT", str(root))

    printed = []
    review.run_review(cfg, reviewer="tester", input_fn=lambda _: "x", print_fn=printed.append)
    out = "\n".join(printed)
    assert "session: Debug the flaky build" in out
    assert "user asked:      why does the build log say line 30?" in out
    assert "claude intended: Let me look at the log." in out
    assert "Claude SAW 2 region(s), 50 lines:" in out and "lines 1-25: line 1 of" in out and "lines 76-100: line 76 of" in out
    assert "Claude LOST 1 region(s), 50 lines:" in out and "--- hidden lines 26-75 ---" in out
    assert "what Claude did next:" in out and "1. said: Found it on line 30 of the log." in out
    assert "2. Edit build.py  replacing: line 30 of the big build log" in out
    assert "automatic check: a later action reused a line from hidden lines 26-50" in out


def test_review_follows_tool_calls_into_subagent_transcripts(cfg, fake_judge_cls, tmp_path, monkeypatch):
    make_stub(cfg, fake_judge_cls, "t1")  # no agent_id recorded: the review must scan the subagent files
    root = tmp_path / "projects"
    sub = root / "C--proj" / "sess-1" / "subagents"
    sub.mkdir(parents=True)
    (root / "C--proj" / "sess-1.jsonl").write_text(json.dumps({"type": "custom-title", "customTitle": "Novel run"}) + "\n", encoding="utf-8")
    entries = [
        {"type": "user", "isSidechain": True, "agentId": "a1", "message": {"role": "user", "content": "Phase 20: review the world bible for load-bearing facts."}},
        {"type": "assistant", "isSidechain": True, "agentId": "a1", "message": {"role": "assistant", "content": [{"type": "text", "text": "Reading the log first."}]}},
        {"type": "assistant", "isSidechain": True, "agentId": "a1", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat big.log"}}]}},
        {"type": "user", "isSidechain": True, "agentId": "a1", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}]},
         "toolUseResult": {"stdout": numbered(100), "stderr": "", "interrupted": False, "isImage": False}},
        {"type": "assistant", "isSidechain": True, "agentId": "a1", "message": {"role": "assistant", "content": [{"type": "text", "text": "Nothing relevant in the log."}]}},
    ]
    (sub / "agent-a1.jsonl").write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    (sub / "agent-a1.meta.json").write_text(json.dumps({"agentType": "world-review", "description": "World review v2"}), encoding="utf-8")
    monkeypatch.setenv("WINNOW_TRANSCRIPTS_ROOT", str(root))

    printed = []
    review.run_review(cfg, reviewer="tester", input_fn=lambda _: "y", print_fn=printed.append)
    out = "\n".join(printed)
    assert "session: Novel run" in out
    assert "run by subagent: world-review (World review v2)" in out
    assert "user asked:      Phase 20: review the world bible" in out
    assert "1. said: Nothing relevant in the log." in out
    assert "automatic check: none of Claude's next 1 actions" in out


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
