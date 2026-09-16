import json

from winnow.transcript import read_task


def write_transcript(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_reads_last_user_request_and_assistant_intent(tmp_path):
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {"type": "user", "message": {"role": "user", "content": "first request"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "old plan"}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "fix the login bug"}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "<system-reminder>ignore</system-reminder>"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "I'll read auth.py"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}]}},
            {"type": "last-prompt", "prompt": "noise"},
        ],
    )
    task = read_task(str(path))
    assert task.user_request == "fix the login bug"
    assert task.assistant_intent == "I'll read auth.py"
    assert not task.is_empty


def test_new_user_turn_resets_assistant_intent(tmp_path):
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "stale"}]}},
            {"type": "user", "message": {"role": "user", "content": "new request"}},
        ],
    )
    task = read_task(str(path))
    assert task.user_request == "new request" and task.assistant_intent == ""


def test_missing_transcript_is_empty_task():
    assert read_task(None).is_empty
    assert read_task("Z:/does/not/exist.jsonl").is_empty


def test_tail_reading_handles_large_files(tmp_path):
    path = tmp_path / "big.jsonl"
    entries = [{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "x" * 5000}]}} for _ in range(600)]
    entries.append({"type": "user", "message": {"role": "user", "content": "the real ask"}})
    write_transcript(path, entries)
    assert read_task(str(path), max_bytes=100_000).user_request == "the real ask"
