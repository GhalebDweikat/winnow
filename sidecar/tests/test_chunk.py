from winnow.chunk import chunk, group_contiguous


def numbered(n: int) -> str:
    return "\n".join(f"line {i}" for i in range(1, n + 1))


def test_round_trip_reproduces_text():
    text = numbered(137)
    blocks = chunk(text, block_lines=25)
    assert "\n".join(b.text for b in blocks) == text
    assert [b.id for b in blocks][:3] == ["b001", "b002", "b003"]
    assert blocks[0].start == 1 and blocks[0].end == 25
    assert blocks[-1].end == 137


def test_breaks_early_at_blank_line_once_half_full():
    lines = [f"l{i}" for i in range(1, 15)] + [""] + [f"m{i}" for i in range(1, 5)]
    blocks = chunk("\n".join(lines), block_lines=25)
    assert blocks[0].end == 15  # closed at the blank line (index 15 >= 25 // 2)
    assert blocks[1].start == 16


def test_max_blocks_grows_block_size():
    blocks = chunk(numbered(1000), block_lines=1, max_blocks=50)
    assert len(blocks) <= 50


def test_empty_text_has_no_blocks():
    assert chunk("") == []


def test_group_contiguous():
    blocks = chunk(numbered(150), block_lines=25)
    groups = group_contiguous([blocks[1], blocks[2], blocks[5]])
    assert [[b.id for b in g] for g in groups] == [["b002", "b003"], ["b006"]]
