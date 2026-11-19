from state import LocalStore, lock_is_free, merge_bot_state, merge_prices, merge_products


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
