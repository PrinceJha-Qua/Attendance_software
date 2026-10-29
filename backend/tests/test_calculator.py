import pytest

from app.calculator import compute


@pytest.mark.parametrize("A,T,r,S,exp", [
    (27, 34, 75, None, {"pct": 79.4, "status": "safe", "skip_now": 2, "need_in_a_row": None}),             # worked example 1
    (20, 30, 75, None, {"pct": 66.7, "status": "below", "skip_now": None, "need_in_a_row": 10}),           # worked example 2
    (0, 0, 75, None, {"pct": None, "status": "none", "skip_now": None, "need_in_a_row": None}),
    (3, 4, 75, None, {"pct": 75.0, "status": "borderline", "skip_now": 0, "need_in_a_row": None}),         # exactly on the line
    (28, 34, 75, None, {"status": "safe", "skip_now": 3}),
    (26, 34, 75, None, {"status": "borderline", "skip_now": 0}),                                           # 26/.75=34.67 -> 34-34
    (10, 10, 75, None, {"pct": 100.0, "status": "safe", "skip_now": 3}),
    (0, 5, 75, None, {"status": "below", "need_in_a_row": 15}),
    (5, 10, 100, None, {"status": "below", "need_in_a_row": None}),                                        # r = 100%: can never recover
    (10, 10, 100, None, {"status": "borderline", "skip_now": 0}),
    (10, 10, 0, None, {"status": "safe", "skip_now": None}),                                               # r = 0: no limit
])
def test_table(A, T, r, S, exp):
    got = compute(A, T, r, S)
    for k, v in exp.items():
        assert got[k] == v, (k, got)


def test_planned_total():
    g = compute(20, 24, 75, 40)           # must reach ceil(30)=30 of 40; remaining 16; must_attend 10 -> can skip 6
    assert g["can_skip_until_end"] == 6 and g["reachable"] is True
    g = compute(10, 30, 75, 40)           # needs 30, has 10 + 10 left -> unreachable
    assert g["status"] == "below" and g["reachable"] is False
    assert compute(20, 24, 75, 10)["planned_total"] is None            # plan smaller than conducted is ignored
    assert compute(0, 0, 75, 40)["can_skip_until_end"] == 10           # 40 - ceil(30)


def test_no_float_drift():
    # 0.1-style percentages must not flip results
    assert compute(33, 44, 75)["skip_now"] == 0 and compute(33, 44, 75)["status"] == "borderline"
    assert compute(66, 100, 66.0)["skip_now"] == 0
