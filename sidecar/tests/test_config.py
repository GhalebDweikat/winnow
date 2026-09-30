import os
import stat

import pytest

from winnow.config import Config, credential_status, load_env_file, secure_home


def test_env_file_loads_without_overriding(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("WINNOW_JUDGE", raising=False)  # conftest sets it; the file should win here
    monkeypatch.setenv("ANTHROPIC_API_KEY", "already-there")
    env = tmp_path / "env"
    env.write_text(
        "# keys for winnow\n"
        "TYPESAFE_API_KEY=ts-secret-123\n"
        "export ANTHROPIC_API_KEY='from-file'\n"
        'WINNOW_JUDGE="adapter"\n'
        "not a valid line\n"
        "=novalue\n",
        encoding="utf-8",
    )
    loaded = load_env_file(env)
    assert loaded == ["TYPESAFE_API_KEY", "WINNOW_JUDGE"]
    assert os.environ["TYPESAFE_API_KEY"] == "ts-secret-123"
    assert os.environ["ANTHROPIC_API_KEY"] == "already-there"
    assert os.environ["WINNOW_JUDGE"] == "adapter"
    assert Config.from_env().judge == "adapter"


def test_env_file_missing_is_fine(tmp_path):
    assert load_env_file(tmp_path / "nope") == []


def test_credential_status_redacts(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-abcdefghijklmnop")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status = credential_status()
    assert status["TYPESAFE_API_KEY"].startswith("set (ts-abc")
    assert "ijklmnop" not in status["TYPESAFE_API_KEY"]
    assert status["ANTHROPIC_API_KEY"] == "not set"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits; the Windows profile is private already")
def test_secure_home_keeps_the_cache_private(tmp_path, monkeypatch):
    """~/.winnow holds the text of everything winnow hid, the decision log and the keys. It was
    created 0755 with 0644 files, readable by any other user on a shared machine (issue #1)."""
    home = tmp_path / "home"
    home.mkdir(mode=0o755)  # an install that predates the fix
    monkeypatch.setenv("WINNOW_HOME", str(home))
    old = os.umask(0o022)
    try:
        secure_home()
        assert stat.S_IMODE(home.stat().st_mode) == 0o700  # existing install tightened
        new_file = home / "cache.json"
        new_file.write_text("{}")
        assert stat.S_IMODE(new_file.stat().st_mode) == 0o600  # and new files start private
    finally:
        os.umask(old)
