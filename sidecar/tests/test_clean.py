import os
import time

from winnow import cache


def test_clean_removes_old_and_oversized_entries(cfg):
    cfg.cache_dir.mkdir(parents=True)
    old = cfg.cache_dir / "old000000000.json"
    old.write_text("x" * 100, encoding="utf-8")
    ancient = time.time() - 40 * 86400
    os.utime(old, (ancient, ancient))
    fresh = [cfg.cache_dir / f"fresh{i:08d}.json" for i in range(3)]
    for i, path in enumerate(fresh):
        path.write_text("y" * 1000, encoding="utf-8")
        t = time.time() - i * 60  # fresh0 newest
        os.utime(path, (t, t))
    markers = cfg.home / "notified"
    markers.mkdir(parents=True)
    stale = markers / "old-session"
    stale.touch()
    os.utime(stale, (ancient, ancient))

    preview = cache.clean(cfg, older_than_days=30, max_mb=2500 / (1024 * 1024), dry_run=True)
    assert preview["dry_run"] and old.exists()
    assert preview["deleted"] == 2  # the old one, plus the oldest fresh one to get under 2500 bytes

    result = cache.clean(cfg, older_than_days=30, max_mb=2500 / (1024 * 1024))
    assert not old.exists()
    assert fresh[0].exists() and fresh[1].exists() and not fresh[2].exists()
    assert result["entries_after"] == 2 and result["bytes_after"] == 2000
    assert result["stale_session_markers_removed"] == 1 and not stale.exists()


def test_clean_on_empty_home_is_fine(cfg):
    assert cache.clean(cfg)["deleted"] == 0
