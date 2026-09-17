import pytest

from winnow.chunk import chunk
from winnow.questions import QUESTION_SETS, build_questions


def test_every_question_set_builds_one_noul_per_block_plus_error_gate():
    blocks = chunk("\n".join(f"line {i}" for i in range(1, 60)), block_lines=25)
    for name in QUESTION_SETS:
        questions = build_questions(name, blocks)
        assert set(questions) == {b.id for b in blocks} | {"error_present"}


def test_structured_set_uses_object_criteria():
    blocks = chunk("a\nb", block_lines=25)
    q = build_questions("structured", blocks)["b001"]
    assert isinstance(q.criteria["true"], dict) and "examples" in q.criteria["true"]
    assert "not_for" in q.criteria["false"]


def test_unknown_set_is_an_error():
    with pytest.raises(ValueError):
        build_questions("nope", chunk("a", block_lines=25))
