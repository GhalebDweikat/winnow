from winnow import cache, log
from winnow.config import Config
from winnow.hooks import Runtime, post_tool_use, user_prompt_submit


def numbered(n: int) -> str:
    return "\n".join(f"line {i}" for i in range(1, n + 1))


def bash_payload(text: str):
    return {
        "session_id": "s",
        "tool_use_id": "t",
        "tool_name": "Bash",
        "tool_input": {"command": "cat big.log"},
        "tool_response": {"stdout": text, "stderr": "", "interrupted": False, "isImage": False},
        "transcript_path": None,
    }


def test_shadow_judges_and_logs_but_never_rewrites(monkeypatch, fake_judge_cls, fake_summarizer_cls):
    monkeypatch.setenv("WINNOW_MODE", "shadow")
    cfg = Config.from_env()
    assert cfg.shadow
    judge = fake_judge_cls({"b001": 0.9, "b002": 0.0, "b003": 0.0, "b004": 0.9, "error_present": 0.0})
    summarizer = fake_summarizer_cls()

    out = post_tool_use(bash_payload(numbered(100)), Runtime(cfg, judge, summarizer))

    assert out is None  # Claude sees the original
    assert len(judge.calls) == 1  # but the judge really ran
    assert summarizer.calls == []  # and no money was spent on summaries
    event = list(log.read_events(cfg))[-1]
    assert event["mode"] == "shadow" and event["would_rewrite"] is True and event["rewritten"] is False
    assert event["n_hidden"] == 2 and event["chars_after"] < event["chars_before"]
    assert cache.load(cfg, event["key"])["text"] == numbered(100)  # inspectable via winnow recall

    stats = log.stats(cfg)
    assert stats["shadow_outputs_judged"] == 1 and stats["shadow_would_rewrite"] == 1
    assert stats["shadow_chars_would_save"] > 0
    assert stats["outputs_judged"] == 0 and stats["outputs_rewritten"] == 0


def test_shadow_prompt_hook_logs_choice_without_injecting(monkeypatch, fake_judge_cls, tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "deploy.md").write_text("how we deploy", encoding="utf-8")
    monkeypatch.setenv("WINNOW_CONTEXT_DIRS", str(ctx))
    monkeypatch.setenv("WINNOW_MODE", "shadow")
    cfg = Config.from_env()

    out = user_prompt_submit({"prompt": "how do I deploy this?", "cwd": str(tmp_path)}, Runtime(cfg, fake_judge_cls({"deploy": 0.9}), None))

    assert out is None
    event = list(log.read_events(cfg))[-1]
    assert event["would_inject"] is True and event["chosen"] == ["deploy"]
