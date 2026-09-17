from __future__ import annotations

from typing import Any, Mapping

import pytest

from winnow.config import Config
from winnow.judge import JudgeResult


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WINNOW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WINNOW_JUDGE", "off")
    monkeypatch.setenv("WINNOW_SUMMARY", "0")
    monkeypatch.setenv("WINNOW_MIN_CHARS", "10")
    monkeypatch.setenv("WINNOW_DROP", "0.3")  # the fixtures were written against this threshold; the shipped default is 0.1
    monkeypatch.delenv("WINNOW_CONTEXT_DIRS", raising=False)
    yield


@pytest.fixture
def cfg() -> Config:
    return Config.from_env()


class FakeJudge:
    """Returns fixed probabilities; anything unspecified is 0.9 (kept)."""

    name = "fake"

    def __init__(self, probabilities: dict[str, float] | None = None, default: float = 0.9) -> None:
        self.probabilities = probabilities or {}
        self.default = default
        self.calls: list[tuple[Any, Mapping[str, Any]]] = []

    def nouls(self, state: Any, questions: Mapping[str, Any]) -> JudgeResult:
        self.calls.append((state, questions))
        return JudgeResult(
            probabilities={k: self.probabilities.get(k, self.default) for k in questions},
            model="fake",
            input_tokens=123,
            output_tokens=0,
            latency_ms=1,
        )


class FakeSummarizer:
    name = "fake"

    def __init__(self, text: str = "fake summary") -> None:
        self.text = text
        self.calls: list[str] = []

    def summarize(self, text: str, task, describe: str) -> str | None:
        self.calls.append(text)
        return self.text


@pytest.fixture
def fake_judge_cls():
    return FakeJudge


@pytest.fixture
def fake_summarizer_cls():
    return FakeSummarizer
