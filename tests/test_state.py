import pytest
import requests

import state
from config import GIST_WRITE_BACKOFF, GIST_WRITE_RETRIES
from state import GistStore, LocalStore, lock_is_free, merge_bot_state, merge_prices, merge_products


class FakeResp:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def test_local_store_roundtrip(tmp_path):
    s = LocalStore(root=tmp_path / "state").load()
    assert s.read("products.json") is None
    s.write("products.json", {"products": [1, 2, 3]})
    assert s.read("products.json") == {"products": [1, 2, 3]}
    s.write("prices.json", {"u": {"price": 9.5}})
    assert s.read("prices.json")["u"]["price"] == 9.5


def test_lock_is_free():
    assert lock_is_free(None, "r1", "2026-01-01T00:00:00+00:00")
    assert lock_is_free({}, "r1", "2026-01-01T00:00:00+00:00")
    held = {"runner": "r2", "expires": "2026-01-01T00:05:00+00:00"}
    assert not lock_is_free(held, "r1", "2026-01-01T00:00:00+00:00")   # held, unexpired
    assert lock_is_free(held, "r2", "2026-01-01T00:00:00+00:00")       # our own lock
    assert lock_is_free(held, "r1", "2026-01-01T00:06:00+00:00")       # expired
    assert lock_is_free({"runner": "r2"}, "r1", "2026-01-01T00:00:00+00:00")  # no expiry


def test_merge_prices_fresher_wins_and_histories_union():
    theirs = {"u": {"price": 10.0, "last_checked": "2026-01-01T10:00:00+00:00",
                    "history": [{"t": "2026-01-01T10:00:00+00:00", "p": 10.0}]}}
    ours = {"u": {"price": 11.0, "last_checked": "2026-01-01T09:00:00+00:00",
                  "history": [{"t": "2026-01-01T09:00:00+00:00", "p": 11.0}]}}
    out = merge_prices(theirs, ours)
    assert out["u"]["price"] == 10.0                        # theirs is fresher
    assert len(out["u"]["history"]) == 2                    # but our point survives
    assert [h["p"] for h in out["u"]["history"]] == [11.0, 10.0]


def test_merge_prices_union_and_empty_sides():
    a = {"u": {"price": 1.0, "last_checked": "2026-01-01T10:00:00+00:00",
               "history": [{"t": "2026-01-01T10:00:00+00:00", "p": 1.0}]}}
    b = {"v": {"price": 2.0, "last_checked": "2026-01-01T10:00:00+00:00"}}
    assert set(merge_prices(a, b)) == {"u", "v"}
    assert merge_prices(None, a) == a
    assert merge_prices(a, None) == a


def test_merge_products_union_ours_wins():
    theirs = {"version": 2, "products": [{"id": "a", "name": "old"}]}
    ours = {"version": 2, "products": [{"id": "a", "name": "new"}, {"id": "b", "name": "x"}]}
    out = merge_products(theirs, ours)
    assert [e["name"] for e in out["products"]] == ["new", "x"]
    assert out["version"] == 2


def test_merge_bot_state_max_offset():
    theirs = {"update_offset": 5, "chat_id": 1}
    ours = {"update_offset": 9}
    out = merge_bot_state(theirs, ours)
    assert out["update_offset"] == 9 and out["chat_id"] == 1
    assert merge_bot_state(ours, theirs)["update_offset"] == 9


def test_merge_products_ignores_v1_strays():
    # an old-code runner wrote flat v1 entries into the gist between load and write;
    # they must not be unioned as products
    theirs = {"products": [{"id": "x", "url": "u", "name": "legacy"}]}   # no offers key
    ours = {"version": 2, "products": [{"id": "x", "name": "new",
                                        "offers": [{"url": "u", "store": "s", "label": "s"}]}]}
    out = merge_products(theirs, ours)
    assert len(out["products"]) == 1 and out["products"][0]["offers"]


def test_merge_bot_state_new_keys():
    theirs = {"update_offset": 5,
              "pending_alerts": {"2": [{"text": "a", "kb": None}]},
              "pending_adds": {"2": {"key": "k1", "query": "x", "candidates": []}},
              "usage": {"month": "2026-11", "credits": 10, "warned": False}}
    ours = {"update_offset": 9,
            "pending_alerts": {"3": [{"text": "b", "kb": None}]},
            "pending_adds": {},
            "usage": {"month": "2026-11", "credits": 14, "warned": True}}
    out = merge_bot_state(theirs, ours)
    assert out["update_offset"] == 9
    assert set(out["pending_alerts"]) == {"2", "3"}      # union across runners
    assert out["pending_adds"]["2"]["key"] == "k1"       # kept when ours is empty
    assert out["usage"]["credits"] == 14                 # max within the month
    assert out["usage"]["warned"] is True
    # month rollover: ours is newer → its (fresh) usage wins outright
    ours["usage"] = {"month": "2026-12", "credits": 1, "warned": False}
    out = merge_bot_state(theirs, ours)
    assert out["usage"]["month"] == "2026-12"
    # older ours usage loses to theirs
    ours["usage"] = {"month": "2026-10", "credits": 99, "warned": True}
    out = merge_bot_state(theirs, ours)
    assert out["usage"]["month"] == "2026-11"


# ---------------------------------------------------------------------------
# GistStore.write retries (the 2026-11-25 15:14 UTC run lost prices.json to a 409
# that GitHub returned while still committing the preceding products.json write)
# ---------------------------------------------------------------------------

def test_gist_write_retries_409_then_succeeds(monkeypatch):
    s = GistStore("gid", "tok")
    sleeps = []
    monkeypatch.setattr(state.time, "sleep", lambda sec: sleeps.append(sec))
    calls = []
    statuses = [409, 409, 200]

    def fake_patch(*args, **kwargs):
        r = FakeResp(statuses.pop(0))
        calls.append(r)
        return r

    monkeypatch.setattr(state.requests, "patch", fake_patch)
    s.write("prices.json", {"u": {"price": 1.0}})
    assert len(calls) == 3
    assert sleeps == [GIST_WRITE_BACKOFF, GIST_WRITE_BACKOFF * 2]   # exponential


def test_gist_write_raises_after_persistent_409(monkeypatch):
    s = GistStore("gid", "tok")
    monkeypatch.setattr(state.time, "sleep", lambda sec: None)
    calls = []

    def fake_patch(*args, **kwargs):
        r = FakeResp(409)
        calls.append(r)
        return r

    monkeypatch.setattr(state.requests, "patch", fake_patch)
    with pytest.raises(requests.HTTPError):
        s.write("prices.json", {"u": {"price": 1.0}})
    assert len(calls) == GIST_WRITE_RETRIES + 1


def test_gist_write_does_not_retry_permanent_errors(monkeypatch):
    s = GistStore("gid", "tok")
    monkeypatch.setattr(state.time, "sleep", lambda sec: None)
    calls = []

    def fake_patch(*args, **kwargs):
        r = FakeResp(404)
        calls.append(r)
        return r

    monkeypatch.setattr(state.requests, "patch", fake_patch)
    with pytest.raises(requests.HTTPError):
        s.write("prices.json", {"u": {"price": 1.0}})
    assert len(calls) == 1      # 404 is not transient — no retry, no backoff


def test_gist_write_sends_content_as_json(monkeypatch):
    s = GistStore("gid", "tok")
    monkeypatch.setattr(state.time, "sleep", lambda sec: None)
    seen = {}

    def fake_patch(*args, **kwargs):
        seen.update(kwargs)
        return FakeResp(200)

    monkeypatch.setattr(state.requests, "patch", fake_patch)
    s.write("prices.json", {"u": {"price": 1.0}})
    assert seen["json"]["files"]["prices.json"]["content"] == \
        '{\n  "u": {\n    "price": 1.0\n  }\n}'
