"""Multi-user command/callback behavior — Telegram API calls captured, no network."""
import telegram_io
import users


def U(ud, cid):
    u = users.get(ud, cid)
    assert u is not None
    return u



class FakeTG:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None, **kw):
        self.calls.append((url, json))

        class R:
            ok = True

            def json(self):
                return {"ok": True, "result": []}

        return R()

    def sent(self, method):
        return [j for u, j in self.calls if f"/{method}" in u]


def wire(monkeypatch):
    fake = FakeTG()
    monkeypatch.setattr(telegram_io.requests, "post", fake.post)
    return fake


def ud_fixture():
    ud = users.ensure(None)
    owner = users.new_user(1, "Leon", "ldigah", users.ROLE_OWNER, users.ST_ACTIVE)
    member = users.new_user(2, "Ada", "ada")
    ud["users"] += [owner, member]
    return ud


def products_fixture():
    prod = {"id": "jbl-flip-7", "name": "JBL Flip 7", "threshold_pct": None,
            "added_by": 2, "atl": None, "last_min": None,
            "offers": [{"url": "https://salidzini.lv/search?q=jbl", "store": "salidzini.lv",
                        "label": "salidzini.lv"}]}
    return {"products": [prod]}


def msg(text, chat_id=2):
    return {"message": {"chat": {"id": chat_id}, "text": text, "from": {"first_name": "X"}}}


# ---------------------------------------------------------------------------
# payload (Worker-dispatched commands)
# ---------------------------------------------------------------------------

def test_payload_updates_carries_confirmed():
    event = {"client_payload": {"commands": [
        {"text": "/remove jbl", "chat_id": 2, "confirmed": True},
        {"text": "/prices", "chat_id": 2},
    ]}}
    ups = telegram_io.payload_updates(event)
    assert ups[0]["confirmed"] is True
    assert ups[1]["confirmed"] is False


# ---------------------------------------------------------------------------
# keyboards
# ---------------------------------------------------------------------------

def test_callback_data_fits_telegram_limit():
    slugs = ["jbl-flip-7", "ugreen-lp923-magsafe-car-phone-holder-very-long-slug"]
    for kb in [telegram_io.kb_alert(s) for s in slugs] \
        + [telegram_io.kb_confirm_remove(s) for s in slugs] \
        + [telegram_io.kb_confirm_threshold(s, 12.5) for s in slugs]:
        for row in kb:
            for btn in row:
                assert len(btn["callback_data"]) <= 64


def test_kb_candidates_shape():
    kb = telegram_io.kb_candidates("k.123", [
        {"name": "JBL Flip 7", "min_price": 73.0, "count": 12}])
    flat = [b for row in kb for b in row]
    assert len(flat) == 3  # candidate + add-as-is + cancel
    assert flat[0]["callback_data"] == "add:0:k.123"
    assert flat[1]["callback_data"] == "add:-1:k.123"


# ---------------------------------------------------------------------------
# strangers → request → approve (Q1/Q5/Q35)
# ---------------------------------------------------------------------------

def test_stranger_gets_intro_and_owner_pinged(monkeypatch):
    fake = wire(monkeypatch)
    ud = ud_fixture()
    out = telegram_io.handle_update(msg("/prices", chat_id=77), products_fixture(),
                                    {}, {}, ud)
    assert out == 77
    sends = fake.sent("sendMessage")
    assert any((s.get("reply_markup") or {}).get("inline_keyboard")
               and "Request access" in
               s["reply_markup"]["inline_keyboard"][0][0]["text"] for s in sends)
    assert any("requests access" in s["text"] for s in sends)  # owner ping
    assert U(ud, 77)["status"] == users.ST_PENDING
    # pending user asking again doesn't duplicate the request
    n = len(ud["users"])
    telegram_io.handle_update(msg("/prices", chat_id=77), products_fixture(), {}, {}, ud)
    assert len(ud["users"]) == n


def test_owner_approval_via_callback(monkeypatch):
    fake = wire(monkeypatch)
    ud = ud_fixture()
    users.request_access(ud, 77, "New", "new")
    cb = {"data": "apr:77", "id": "cb1", "message": {"chat": {"id": 1}}}
    out = telegram_io.handle_callback(cb, products_fixture(), {}, {}, ud)
    assert out == 1
    assert U(ud, 77)["status"] == users.ST_ACTIVE
    assert any("You're in" in s["text"] for s in fake.sent("sendMessage"))
    # decline flow on a second stranger
    users.request_access(ud, 88, "Nope", "")
    telegram_io.handle_callback({"data": "dec:88", "id": "cb2",
                                 "message": {"chat": {"id": 1}}},
                                products_fixture(), {}, {}, ud)
    assert U(ud, 88)["status"] == users.ST_BLOCKED


# ---------------------------------------------------------------------------
# watch/mute buttons and commands (Q3/Q15)
# ---------------------------------------------------------------------------

def test_watch_command_and_toggle_callbacks(monkeypatch):
    fake = wire(monkeypatch)
    ud = ud_fixture()
    prods = products_fixture()
    telegram_io.handle_update(msg("/watch jbl"), prods, {}, {}, ud)
    assert U(ud, 2)["watchlist"] == ["jbl-flip-7"]
    telegram_io.handle_update(msg("/watch"), prods, {}, {}, ud)  # list renders
    # mute via button
    telegram_io.handle_callback({"data": "mu:jbl-flip-7", "id": "c",
                                 "message": {"chat": {"id": 2}}}, prods, {}, {}, ud)
    assert "jbl-flip-7" in U(ud, 2)["muted"]
    # toggle back off
    telegram_io.handle_callback({"data": "mu:jbl-flip-7", "id": "c",
                                 "message": {"chat": {"id": 2}}}, prods, {}, {}, ud)
    assert "jbl-flip-7" not in U(ud, 2)["muted"]
    assert fake.sent("sendMessage")  # confirmations went out


def test_watch_all_toggle_owner_only(monkeypatch):
    wire(monkeypatch)
    ud = ud_fixture()
    telegram_io.handle_update(msg("/watchall", chat_id=2), products_fixture(), {}, {}, ud)
    assert U(ud, 2)["watch_all"] is False  # member: unchanged (refused)
    telegram_io.handle_update(msg("/watchall", chat_id=1), products_fixture(), {}, {}, ud)
    assert U(ud, 1)["watch_all"] is False  # owner: True → False


# ---------------------------------------------------------------------------
# confirmations (Q31)
# ---------------------------------------------------------------------------

def test_remove_requires_confirm_then_callback_removes(monkeypatch):
    wire(monkeypatch)
    ud = ud_fixture()
    prods = products_fixture()
    telegram_io.handle_update(msg("/remove jbl", chat_id=2), prods, {}, {}, ud)
    assert len(prods["products"]) == 1  # not yet
    telegram_io.handle_callback({"data": "ok:jbl-flip-7", "id": "c",
                                 "message": {"chat": {"id": 2}}}, prods, {}, {}, ud)
    assert prods["products"] == []
    assert U(ud, 2)["watchlist"] == []  # cleanup of watchlists


def test_remove_denied_for_non_owner_non_adder(monkeypatch):
    wire(monkeypatch)
    ud = ud_fixture()
    users.request_access(ud, 3, "Bob", "")
    users.approve(ud, 3)
    prods = products_fixture()
    prods["products"][0]["added_by"] = 2  # Ada added it
    telegram_io.handle_update(msg("/remove jbl", chat_id=3), prods, {}, {}, ud)
    assert len(prods["products"]) == 1


def test_threshold_confirmation(monkeypatch):
    wire(monkeypatch)
    ud = ud_fixture()
    prods = products_fixture()
    telegram_io.handle_update(msg("/threshold jbl 12", chat_id=2), prods, {}, {}, ud)
    assert prods["products"][0]["threshold_pct"] is None  # awaiting confirm
    telegram_io.handle_callback({"data": "th:jbl-flip-7:12", "id": "c",
                                 "message": {"chat": {"id": 2}}}, prods, {}, {}, ud)
    assert prods["products"][0]["threshold_pct"] == 12


# ---------------------------------------------------------------------------
# guided add (Q30)
# ---------------------------------------------------------------------------

def test_add_delegates_to_stager(monkeypatch):
    wire(monkeypatch)
    ud = ud_fixture()
    prods = products_fixture()
    seen = []

    def stager(chat_id, query, user, bot_state):
        seen.append((chat_id, query))

    telegram_io.handle_update(msg("/add anker soundcore"), prods, {}, {}, ud,
                              add_stager=stager)
    assert seen == [(2, "anker soundcore")]
    assert len(prods["products"]) == 1  # staged, not created


def test_pick_creates_from_candidates(monkeypatch):
    wire(monkeypatch)
    ud = ud_fixture()
    prods = products_fixture()
    bot_state = {"pending_adds": {"2": {"key": "k.1", "query": "anker",
                                        "candidates": [
                                            {"name": "Anker Soundcore 2", "min_price": 35.0,
                                             "count": 9}]}}}
    # unconfirmed pick (no Worker flag) must not create
    telegram_io.handle_update(msg("/pick k.1 0", chat_id=2), prods, {}, bot_state, ud)
    assert len(prods["products"]) == 1
    assert bot_state["pending_adds"]["2"]["key"] == "k.1"  # not consumed yet
    confirmed = msg("/pick k.1 0", chat_id=2)
    confirmed["confirmed"] = True  # flag the Worker puts on button-triggered dispatches
    telegram_io.handle_update(confirmed, prods, {}, bot_state, ud)
    assert len(prods["products"]) == 2
    new = prods["products"][-1]
    assert new["name"] == "Anker Soundcore 2" and new["added_by"] == 2
    assert "anker-soundcore-2" in U(ud, 2)["watchlist"]  # auto-watch


def test_member_quota_blocks_add(monkeypatch):
    wire(monkeypatch)
    ud = ud_fixture()
    U(ud, 2)["quota"] = 0
    prods = products_fixture()
    telegram_io.handle_update(msg("/add new thing", chat_id=2), prods, {}, {}, ud)
    assert len(prods["products"]) == 1


# ---------------------------------------------------------------------------
# settings panel (Q32)
# ---------------------------------------------------------------------------

def test_settings_callback_cycles_prefs(monkeypatch):
    wire(monkeypatch)
    ud = ud_fixture()
    u = U(ud, 2)
    assert u["prefs"]["digest_hour_utc"] == 21
    telegram_io.handle_callback({"data": "st:hour", "id": "c",
                                 "message": {"chat": {"id": 2}}},
                                products_fixture(), {}, {}, ud)
    assert u["prefs"]["digest_hour_utc"] == 5  # wraps to the first run hour
    telegram_io.handle_callback({"data": "st:digest", "id": "c",
                                 "message": {"chat": {"id": 2}}},
                                products_fixture(), {}, {}, ud)
    assert u["prefs"]["digest"] is True
