from winnow import log
from winnow.demo import run_demo


def test_fake_demo_hides_unrelated_blocks_and_keeps_the_retry_section(capsys, cfg):
    assert run_demo(fake=True) == 0
    out = capsys.readouterr().out
    assert "[winnow] Lines" in out and "hidden" in out
    assert "backoff_initial_s" in out  # the section the task is about survives
    assert "(demo summary)" in out
    assert "Restore with: winnow recall" in out


def test_demo_events_are_excluded_from_stats(cfg):
    run_demo(fake=True)
    events = list(log.read_events(cfg))
    assert events and events[-1].get("demo") is True
    assert log.stats(cfg)["outputs_judged"] == 0


def test_live_demo_without_a_judge_explains_itself(capsys, cfg):
    # conftest sets WINNOW_JUDGE=off
    assert run_demo(fake=False) == 1
    assert "WINNOW_JUDGE is off" in capsys.readouterr().out
