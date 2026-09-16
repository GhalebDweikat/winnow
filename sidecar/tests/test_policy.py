from winnow.chunk import chunk
from winnow.policy import decide


def blocks_of(n_lines: int = 100):
    return chunk("\n".join(f"line {i}" for i in range(1, n_lines + 1)), block_lines=25)


def test_prunes_only_below_drop(cfg):
    blocks = blocks_of()
    probs = {"b001": 0.95, "b002": 0.1, "b003": 0.4, "b004": 0.05}
    verdict = decide(blocks, probs, 0.0, cfg)
    assert verdict.reason == "pruned"
    assert [b.id for b in verdict.pruned] == ["b002", "b004"]
    assert [b.id for b in verdict.uncertain] == ["b003"]
    assert [b.id for b in verdict.kept] == ["b001", "b003"]


def test_error_gate_keeps_everything(cfg):
    verdict = decide(blocks_of(), {"b001": 0.0, "b002": 0.0, "b003": 0.0, "b004": 0.0}, 0.8, cfg)
    assert verdict.reason == "error_present"
    assert not verdict.pruned


def test_missing_probability_is_kept(cfg):
    # b003 and b004 were never judged (no probability): they must be kept.
    verdict = decide(blocks_of(), {"b001": 0.0, "b002": 0.0}, 0.0, cfg)
    assert [b.id for b in verdict.pruned] == ["b001", "b002"]
    assert [b.id for b in verdict.kept] == ["b003", "b004"]


def test_below_min_prune_ratio_keeps_everything(cfg):
    blocks = chunk("\n".join(f"line {i}" for i in range(1, 501)), block_lines=25)
    verdict = decide(blocks, {"b001": 0.0}, 0.0, cfg)
    assert verdict.reason == "below_min_prune_ratio"
    assert len(verdict.kept) == len(blocks)
