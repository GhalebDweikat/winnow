import json
import threading
from urllib.request import Request, urlopen

import pytest

from winnow import serve
from winnow.config import Config
from winnow.hooks import Runtime


@pytest.fixture
def server(cfg, fake_judge_cls):
    judge = fake_judge_cls({"b001": 0.9, "b002": 0.0, "b003": 0.0, "b004": 0.9, "error_present": 0.0})
    srv = serve.make_server(0, runtime=Runtime(cfg, judge, None))
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv, judge
    srv.shutdown()
    srv.server_close()


def post(srv, path, obj):
    port = srv.server_address[1]
    req = Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=5) as resp:
        body = resp.read()
        return resp.status, (json.loads(body) if body else None)


def numbered(n):
    return "\n".join(f"line {i}" for i in range(1, n + 1))


def bash_payload(text):
    return {
        "session_id": "s", "tool_use_id": "t", "tool_name": "Bash",
        "tool_input": {"command": "cat big.log"},
        "tool_response": {"stdout": text, "stderr": "", "interrupted": False, "isImage": False},
    }


def test_health_and_small_result_fast_path(server):
    srv, judge = server
    port = srv.server_address[1]
    with urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
        info = json.loads(resp.read())
    assert info["ok"] and info["judge_ready"]
    status, body = post(srv, "/hook/post-tool-use", bash_payload("tiny"))
    assert status == 200 and body is None  # empty 2xx = pass-through
    assert judge.calls == []


def test_large_result_is_judged_and_rewritten(server):
    srv, judge = server
    status, body = post(srv, "/hook/post-tool-use", bash_payload(numbered(100)))
    assert status == 200
    assert body["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "[winnow] Lines 26-75" in body["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert len(judge.calls) == 1
    with urlopen(f"http://127.0.0.1:{srv.server_address[1]}/health", timeout=5) as resp:
        assert json.loads(resp.read())["requests"] == 1


def test_bad_json_and_unknown_paths(server):
    srv, _ = server
    port = srv.server_address[1]
    req = Request(f"http://127.0.0.1:{port}/hook/post-tool-use", data=b"not json", headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(Exception) as excinfo:
        urlopen(req, timeout=5)
    assert "400" in str(excinfo.value)
    req = Request(f"http://127.0.0.1:{port}/nope", data=b"{}", method="POST")
    with pytest.raises(Exception) as excinfo:
        urlopen(req, timeout=5)
    assert "404" in str(excinfo.value)


def test_missing_judge_notifies_once_per_session(cfg, monkeypatch):
    # judge=typesafe with no key: runtime construction fails; first request gets a systemMessage, the second is silent
    monkeypatch.setenv("WINNOW_JUDGE", "typesafe")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    state = serve.State()
    first = serve.handle("post-tool-use", bash_payload(numbered(100)), state)
    assert first and "could not start" in first["systemMessage"]
    second = serve.handle("post-tool-use", bash_payload(numbered(100)), state)
    assert second is None


def test_health_reports_absent_server():
    assert serve.health(1) is None  # nothing listens on port 1
