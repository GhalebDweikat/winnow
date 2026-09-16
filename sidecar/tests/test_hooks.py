import json

from winnow import cache, log
from winnow.hooks import Runtime, post_tool_use, user_prompt_submit


def bash_payload(text: str, **extra):
    return {
        "hook_event_name": "PostToolUse",
        "session_id": "sess",
        "tool_use_id": "toolu_1",
        "tool_name": "Bash",
        "tool_input": {"command": "cat big.log"},
        "tool_response": {"stdout": text, "stderr": "", "interrupted": False, "isImage": False},
        "transcript_path": None,
        **extra,
    }


def numbered(n: int) -> str:
    return "\n".join(f"line {i}" for i in range(1, n + 1))


def test_prunes_low_probability_blocks_with_summary_stub(cfg, fake_judge_cls, fake_summarizer_cls):
    judge = fake_judge_cls({"b001": 0.95, "b002": 0.10, "b003": 0.05, "b004": 0.90, "b005": 0.20, "error_present": 0.02})
    summarizer = fake_summarizer_cls("nothing but noise")
    out = post_tool_use(bash_payload(numbered(120)), Runtime(cfg, judge, summarizer))

    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    new = out["hookSpecificOutput"]["updatedToolOutput"]
    assert set(new) == {"stdout", "stderr", "interrupted", "isImage"}
    stdout = new["stdout"]
    assert "line 1\n" in stdout and "line 100" in stdout
    assert "line 30" not in stdout and "line 120" not in stdout
    assert "[winnow] Lines 26-75 (50 lines) hidden" in stdout
    assert "[winnow] Lines 101-120 (20 lines) hidden" in stdout
    assert "[winnow] Summary: nothing but noise" in stdout
    assert len(summarizer.calls) == 2

    # the judge saw the task/tool/blocks state and one question per block plus the error gate
    state, questions = judge.calls[0]
    assert set(state) == {"task", "tool", "blocks"}
    assert state["tool"]["input"] == {"command": "cat big.log"}
    assert set(questions) == {"b001", "b002", "b003", "b004", "b005", "error_present"}

    # full text is cached under the key printed in the stub, and the decision is logged
    key = stdout.split("cached as key ")[1].split(".")[0]
    entry = cache.load(cfg, key)
    assert entry["text"] == numbered(120)
    assert cache.slice_lines(entry["text"], 26, 27, entry["line_offset"]) == "line 26\nline 27"
    events = list(log.read_events(cfg))
    assert events[-1]["event"] == "post_tool_use" and events[-1]["rewritten"] and events[-1]["key"] == key
    assert log.stats(cfg)["outputs_rewritten"] == 1


def test_error_output_is_never_pruned(cfg, fake_judge_cls):
    judge = fake_judge_cls({"b001": 0.0, "b002": 0.0, "b003": 0.0, "b004": 0.0, "error_present": 0.9})
    assert post_tool_use(bash_payload(numbered(100)), Runtime(cfg, judge, None)) is None
    assert list(log.read_events(cfg))[-1]["reason"] == "error_present"


def test_small_or_unsupported_outputs_pass_through(cfg, fake_judge_cls):
    judge = fake_judge_cls(default=0.0)
    assert post_tool_use(bash_payload("tiny"), Runtime(cfg, judge, None)) is None
    assert post_tool_use({**bash_payload(numbered(100)), "tool_name": "Glob"}, Runtime(cfg, judge, None)) is None
    assert judge.calls == []


def test_judge_failure_passes_through(cfg):
    class Boom:
        name = "boom"

        def nouls(self, state, questions):
            raise RuntimeError("api down")

    assert post_tool_use(bash_payload(numbered(100)), Runtime(cfg, Boom(), None)) is None
    assert list(log.read_events(cfg))[-1]["reason"] == "judge_error"
    assert cfg.error_log_path.exists()


def test_judge_off_is_inert(cfg):
    assert post_tool_use(bash_payload(numbered(100)), Runtime(cfg, None, None)) is None


def test_read_stub_uses_file_line_numbers(cfg, fake_judge_cls):
    payload = {
        "session_id": "s",
        "tool_use_id": "t",
        "tool_name": "Read",
        "tool_input": {"file_path": "C:\\proj\\big.py", "offset": 200},
        "tool_response": {"type": "text", "file": {"filePath": "C:\\proj\\big.py", "content": numbered(100), "numLines": 100, "startLine": 200, "totalLines": 900}},
    }
    judge = fake_judge_cls({"b001": 0.9, "b002": 0.0, "b003": 0.0, "b004": 0.9, "error_present": 0.0})
    out = post_tool_use(payload, Runtime(cfg, judge, None))
    content = out["hookSpecificOutput"]["updatedToolOutput"]["file"]["content"]
    assert "[winnow] Lines 225-274 (50 lines) hidden" in content
    assert "[winnow] Summary unavailable." in content


def test_user_prompt_submit_injects_only_relevant_files(cfg, fake_judge_cls, tmp_path, monkeypatch):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "MEMORY.md").write_text("- index, never a candidate", encoding="utf-8")
    (ctx / "deploy-runbook.md").write_text("---\nname: deploy-runbook\ndescription: how we deploy\n---\nRun make deploy.", encoding="utf-8")
    (ctx / "cat-names.md").write_text("Whiskers, Tom.", encoding="utf-8")
    monkeypatch.setenv("WINNOW_CONTEXT_DIRS", str(ctx))
    from winnow.config import Config

    cfg = Config.from_env()
    judge = fake_judge_cls({"deploy_runbook": 0.92, "cat_names": 0.05})
    out = user_prompt_submit({"prompt": "how do I deploy this to prod?", "cwd": str(tmp_path)}, Runtime(cfg, judge, None))
    ctx_text = out["hookSpecificOutput"]["additionalContext"]
    assert "deploy-runbook" in ctx_text and "Run make deploy." in ctx_text
    assert "Whiskers" not in ctx_text
    state, questions = judge.calls[0]
    assert set(questions) == {"deploy_runbook", "cat_names"}
    assert state["candidates"]["deploy_runbook"]["description"] == "how we deploy"


def test_user_prompt_submit_respects_gate(cfg, fake_judge_cls, tmp_path, monkeypatch):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "a.md").write_text("alpha", encoding="utf-8")
    monkeypatch.setenv("WINNOW_CONTEXT_DIRS", str(ctx))
    from winnow.config import Config

    out = user_prompt_submit({"prompt": "something unrelated entirely", "cwd": str(tmp_path)}, Runtime(Config.from_env(), fake_judge_cls({"a": 0.2}), None))
    assert out is None


def test_hook_output_is_json_serializable(cfg, fake_judge_cls):
    judge = fake_judge_cls({"b001": 0.0, "b002": 0.0, "b003": 0.9, "b004": 0.9, "error_present": 0.0})
    out = post_tool_use(bash_payload(numbered(100)), Runtime(cfg, judge, None))
    json.dumps(out)
