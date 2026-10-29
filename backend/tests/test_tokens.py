import json
import pathlib

from app import tokens

SECRET = b"unit-test-secret"
STEP = 1500


def test_vectors_match_shared_spec():
    vectors = json.loads((pathlib.Path(__file__).parents[2] / "shared/token_vectors.json").read_text())
    for v in vectors["vectors"]:
        assert tokens.make_qr_payload(vectors["secret"].encode(), v["session_id"], v["window"]) == v["payload"]


def test_ok_current_and_previous_window():
    now = 10 * STEP + 100
    w = tokens.window_for(now, STEP)
    for ww in (w, w - 1):
        p = tokens.parse_qr(tokens.make_qr_payload(SECRET, "s1", ww))
        assert tokens.verify(SECRET, p, now, STEP, 1) == "ok"


def test_expired_when_older_than_grace():
    now = 10 * STEP
    p = tokens.parse_qr(tokens.make_qr_payload(SECRET, "s1", 10 - 2))
    assert tokens.verify(SECRET, p, now, STEP, 1) == "expired"


def test_future_window_is_expired_not_ok():
    now = 10 * STEP
    p = tokens.parse_qr(tokens.make_qr_payload(SECRET, "s1", 12))
    assert tokens.verify(SECRET, p, now, STEP, 1) == "expired"


def test_forged_token_and_wrong_session():
    now = 10 * STEP
    good = tokens.make_qr_payload(SECRET, "s1", 10)
    forged = good[:-1] + ("A" if good[-1] != "A" else "B")
    assert tokens.verify(SECRET, tokens.parse_qr(forged), now, STEP) == "invalid"
    moved = good.replace(":s1:", ":s2:")
    assert tokens.verify(SECRET, tokens.parse_qr(moved), now, STEP) == "invalid"
    assert tokens.verify(b"other", tokens.parse_qr(good), now, STEP) == "invalid"


def test_parse_rejects_garbage():
    for bad in ["", "x", "gk1:a:b:c", "gk2:s:1:T", "gk1:s:1", "gk1:s:1:T:extra"]:
        assert tokens.parse_qr(bad) is None
