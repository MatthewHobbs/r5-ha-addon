"""Tests for the util seam: the shared pure primitives (`now_ts` / `iso` / `_num`)."""
import util


def test_num_rounds_and_tolerates_garbage():
    assert util._num("12.345") == 12.35
    assert util._num(None) is None
    assert util._num("not-a-number") is None


def test_now_ts_and_iso():
    assert isinstance(util.now_ts(), float)
    assert util.iso(0) is None
    assert util.iso(1000).startswith("1970-01-01")
