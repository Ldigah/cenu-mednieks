from state import LocalStore


def test_local_store_roundtrip(tmp_path):
    s = LocalStore(root=tmp_path / "state").load()
    assert s.read("products.json") is None
    s.write("products.json", {"products": [1, 2, 3]})
    assert s.read("products.json") == {"products": [1, 2, 3]}
    s.write("prices.json", {"u": {"price": 9.5}})
    assert s.read("prices.json")["u"]["price"] == 9.5
