"""Phase 3: end-to-end i18n (lv /help + acks), deals.json/RSS, register_commands."""

import publish
import telegram_io
import users


def wire(monkeypatch):
    calls = []

    def post(url, json=None, timeout=None, **kw):
        calls.append((url, json))

        class R:
            ok = True

            def json(self):
                return {"ok": True, "result": []}

        return R()

    monkeypatch.setattr(telegram_io.requests, "post", post)
    return calls


def ud_fixture():
    ud = users.ensure(None)
    lv = users.new_user(2, "Anna", "anna")
    users.set_pref(lv, "lang", "lv")
    ud["users"] = [users.new_user(1, "Leon", "ldigah", users.ROLE_OWNER,
                                  users.ST_ACTIVE), lv]
    return ud


def msg(text, chat_id=2):
    return {"message": {"chat": {"id": chat_id}, "text": text,
                        "from": {"first_name": "X"}}}


def products_fixture():
    return {"products": [{"id": "jbl-flip-7", "name": "JBL Flip 7", "tags": ["audio"],
                          "threshold_pct": None, "added_by": None, "atl": None,
                          "last_min": None,
                          "offers": [{"url": "https://s.lv/x", "store": "s.lv",
                                      "label": "s.lv"}]}]}


def test_lv_user_gets_latvian_help_and_acks(monkeypatch):
    calls = wire(monkeypatch)
    ud = ud_fixture()
    prods = products_fixture()
    telegram_io.handle_update(msg("/help"), prods, {}, {}, ud)
    sent = [j for u, j in calls if "/sendMessage" in u]
    assert any("cenu un atlaides mednieks" in s.get("text", "") for s in sent)
    # watch ack in Latvian
    telegram_io.handle_update(msg("/watch jbl"), prods, {}, {}, ud)
    sent = [j for u, j in calls if "/sendMessage" in u]
    assert any("Sekoju <b>JBL Flip 7</b>" in s.get("text", "") for s in sent)


def test_en_user_still_gets_english(monkeypatch):
    calls = wire(monkeypatch)
    ud = ud_fixture()
    prods = products_fixture()
    telegram_io.handle_update(msg("/watch jbl", chat_id=1), prods, {}, {}, ud)
    sent = [j for u, j in calls if "/sendMessage" in u]
    assert any("Watching <b>JBL Flip 7</b>" in s.get("text", "") for s in sent)


def test_target_acks_localized(monkeypatch):
    calls = wire(monkeypatch)
    ud = ud_fixture()
    prods = products_fixture()
    telegram_io.handle_update(msg("/target jbl 65"), prods, {}, {}, ud)
    sent = [j for u, j in calls if "/sendMessage" in u]
    assert any("ziņošu, kad labākā cena" in s.get("text", "") for s in sent)
    telegram_io.handle_update(msg("/target jbl off"), prods, {}, {}, ud)
    sent = [j for u, j in calls if "/sendMessage" in u]
    assert any("Mērķis produktam" in s.get("text", "") for s in sent)


def test_register_commands_scopes(monkeypatch):
    calls = wire(monkeypatch)
    telegram_io.register_commands(owner_chat_id=497422706)
    cmds = [j for u, j in calls if "/setMyCommands" in u]
    assert len(cmds) == 2
    assert {"command": "target", "description": "Ping me when price drops under X"} \
        in cmds[0]["commands"]
    owner_cmds = cmds[1]["commands"]
    assert {"command": "ping", "description": "Worker self-test (owner)"} in owner_cmds
    assert cmds[1]["scope"] == {"type": "chat", "chat_id": 497422706}
    assert {"command": "ping"} not in [{**c} for c in cmds[0]["commands"]]


# ---------------------------------------------------------------------------
# deals.json + RSS (Q25)
# ---------------------------------------------------------------------------

def data_fixture():
    return {
        "generated": "2026-11-19T17:41:04+00:00",
        "tags": {"audio": 1},
        "products": [
            {"id": "hot", "name": "Hot Deal", "image": "https://i/x.jpg",
             "above_atl": 12, "chart": "charts/hot.png", "tags": [],
             "stats": {"best": {"price": 50.0, "store": "24.lv",
                                "url": "https://24.lv/x"}, "avg30": 100.0}},
            {"id": "atl", "name": "ATL Now", "image": None,
             "above_atl": 0, "chart": "charts/atl.png", "tags": [],
             "stats": {"best": {"price": 10.0, "store": "s.lv",
                                "url": "https://s.lv/a"}, "avg30": 10.0}},
            {"id": "boring", "name": "No Deal", "image": None,
             "above_atl": 9, "chart": "charts/boring.png", "tags": [],
             "stats": {"best": {"price": 20.0, "store": "s.lv",
                                "url": "https://s.lv/b"}, "avg30": 20.05}},
        ],
    }


def test_build_deals_ranking():
    deals = publish.build_deals(data_fixture())
    ids = [d["id"] for d in deals["deals"]]
    assert ids == ["hot", "atl"]          # 50% drop first, ATL second, boring out
    hot = deals["deals"][0]
    assert hot["drop_pct"] == 50.0 and hot["is_atl"] is False
    assert deals["deals"][1]["is_atl"] is True


def test_rss_xml_shape():
    deals = publish.build_deals(data_fixture())
    xml = publish.rss_xml(deals)
    assert xml.startswith('<?xml version="1.0"')
    assert "<title>Hot Deal — 50.00 € at 24.lv · −50.0% vs 30-day avg</title>" in xml
    assert "🏆 all-time low" in xml       # atl item title
    assert xml.count("<item>") == 2
