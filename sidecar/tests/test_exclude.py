from winnow.config import Config
from winnow.hooks import Runtime, excluded, post_tool_use, worth_judging


def test_reads_under_winnow_home_are_excluded(cfg):
    inside = cfg.home / "replay" / "sample.md"
    assert excluded("Read", {"file_path": str(inside)}, cfg)
    assert not excluded("Read", {"file_path": str(cfg.home.parent / "elsewhere.md")}, cfg)


def test_winnow_commands_are_excluded(cfg):
    assert excluded("Bash", {"command": "uv run winnow recall abc123"}, cfg)
    assert excluded("Bash", {"command": "winnow stats"}, cfg)
    assert not excluded("Bash", {"command": "cat winnowing.log"}, cfg)  # word boundary
    assert not excluded("Grep", {"pattern": "winnow"}, cfg)


def test_excluded_payload_is_not_worth_judging_and_passes_through(cfg, fake_judge_cls):
    text = "\n".join(f"line {i}" for i in range(1, 200))
    payload = {
        "tool_name": "Read", "session_id": "s", "tool_use_id": "t",
        "tool_input": {"file_path": str(cfg.home / "replay" / "sample.md")},
        "tool_response": {"type": "text", "file": {"filePath": "x", "content": text, "numLines": 199, "startLine": 1, "totalLines": 199}},
    }
    assert not worth_judging(payload, cfg)
    judge = fake_judge_cls(default=0.0)
    assert post_tool_use(payload, Runtime(cfg, judge, None)) is None
    assert judge.calls == []


def test_custom_exclude_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("WINNOW_EXCLUDE_PATHS", str(tmp_path / "secret"))
    cfg = Config.from_env()
    assert excluded("Read", {"file_path": str(tmp_path / "secret" / "k.txt")}, cfg)
    assert not excluded("Read", {"file_path": str(cfg.home / "cache" / "x.json")}, cfg)  # override replaces the default
