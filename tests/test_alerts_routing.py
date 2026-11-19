"""Alert routing (Q3/Q4/Q19) + ScraperAPI budget (Q38) + guided-add candidates (Q30)."""
from datetime import UTC, datetime

import alerts
import users
from stores.base import StoreAdapter


def U(ud, cid):
    u = users.get(ud, cid)
    assert u is not None
    return u



def ud_fixture():
    ud = users.ensure(None)
    owner = users.new_user(1, "Leon", "ldigah", users.ROLE_OWNER, users.ST_ACTIVE)
    member = users.new_user(2, "Ada", "ada")
    users.watch(member, "jbl-flip-7")
    ud["users"] += [owner, member]
    return ud


# ---------------------------------------------------------------------------
# recipients / quiet holds
# ---------------------------------------------------------------------------

def test_recipients_follow_watchlists():
    ud = ud_fixture()
    r = alerts.recipients(ud, "jbl-flip-7")
    assert [u["chat_id"] for u in r] == [1, 2]  # owner watch-all + watcher
    assert [u["chat_id"] for u in alerts.recipients(ud, "other-product")] == [1]
    users.mute(U(ud, 1), "other-product")
    assert alerts.recipients(ud, "other-product") == []


def test_hold_and_flush_quiet_hours():
    ud = ud_fixture()
    member = U(ud, 2)
    users.set_pref(member, "quiet", "23-8")
    bot_state: dict = {}
    night = datetime(2026, 11, 19, 23, 30, tzinfo=UTC)
    morning = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
    # member's quiet window holds; owner (quiet off) is never held
    assert alerts.hold_alert(ud, member, "text", None, bot_state, now=night) is True
    owner = U(ud, 1)
    assert alerts.hold_alert(ud, owner, "text", None, bot_state, now=night) is False
    assert bot_state["pending_alerts"] == {"2": [{"text": "text", "kb": None}]}
    # morning flush releases the member's held alert and empties the queue
    out = alerts.flush_held(ud, bot_state, now=morning)
    assert out == {2: [{"text": "text", "kb": None}]}
    assert bot_state["pending_alerts"] == {}


def test_flush_keeps_still_quiet_chats():
    ud = ud_fixture()
    member = U(ud, 2)
    users.set_pref(member, "quiet", "23-8")
    bot_state = {"pending_alerts": {"2": [{"text": "t", "kb": None}]}}
    still_night = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)
    assert alerts.flush_held(ud, bot_state, now=still_night) == {}
    assert len(bot_state["pending_alerts"]["2"]) == 1


# ---------------------------------------------------------------------------
# budget (Q38)
# ---------------------------------------------------------------------------

def test_bump_usage_rolls_over_monthly():
    u = alerts.bump_usage({}, 3, now=datetime(2026, 11, 5, tzinfo=UTC))
    assert u == {"month": "2026-11", "credits": 3, "warned": False}
    alerts.bump_usage(u, 2, now=datetime(2026, 11, 19, tzinfo=UTC))
    assert u["credits"] == 5
    u["warned"] = True
    alerts.bump_usage(u, 1, now=datetime(2026, 12, 1, tzinfo=UTC))
    assert u["credits"] == 1 and u["warned"] is False  # fresh month resets the warn


def test_budget_line_and_warning_threshold():
    assert alerts.budget_line({}) is None
    assert alerts.budget_line({"month": "1999-01", "credits": 500}) is None
    line = alerts.budget_line({"month": datetime.now(UTC).strftime("%Y-%m"),
                               "credits": 250})
    assert line is not None and "250/1000" in line
    usage = {"month": datetime.now(UTC).strftime("%Y-%m"), "credits": 790,
             "warned": False}
    assert alerts.budget_warning(usage) is None  # 790 < 800
    usage["credits"] = 800
    warn = alerts.budget_warning(usage)
    assert warn and "800/1000" in warn
    assert usage["warned"] is True               # fires once
    assert alerts.budget_warning(usage) is None


def test_digest_lines_filtered_by_watchlist():
    products: dict = {"products": [
        {"id": "jbl-flip-7", "name": "JBL Flip 7", "offers": [], "atl": None},
        {"id": "ugreen-lp923", "name": "UGREEN LP923", "offers": [], "atl": None},
    ]}
    lines = alerts.digest_lines(products, {}, ids=["jbl-flip-7"])
    assert any("JBL Flip 7" in ln for ln in lines)
    assert not any("UGREEN" in ln for ln in lines)
    all_lines = alerts.digest_lines(products, {})
    assert any("UGREEN" in ln for ln in all_lines)


# ---------------------------------------------------------------------------
# guided-add candidate grouping (Q30) — pure store logic
# ---------------------------------------------------------------------------

SALIDZINI_HTML = """<html><head><script type="application/ld+json">{
 "@type":"Product","offers":{"@type":"AggregateOffer","offers":[
  {"@type":"Offer","name":"JBL Flip 7","price":"75.00","category":"Audio"},
  {"@type":"Offer","name":"JBL Flip 7","price":"73.00","category":"Audio"},
  {"@type":"Offer","name":"JBL Flip 7","price":"74.00","category":"Audio"},
  {"@type":"Offer","name":"Akumulator f. JBL Flip","price":"19.85","category":"Batteries"},
  {"@type":"Offer","name":"JBL Flip 7 Case","price":"9.00","category":"Cases"}
 ]}}</script></head></html>"""


def test_salidzini_candidates_group_and_filter():
    from stores.salidzini import SALIDZINI
    cands = SALIDZINI.candidates(SALIDZINI_HTML, limit=3)
    names = [c["name"] for c in cands]
    assert names[0] == "JBL Flip 7"                       # most offers first
    assert cands[0]["min_price"] == 73.0 and cands[0]["count"] == 3
    assert all("Akumulator" not in n for n in names)      # battery rows filtered


CENO_HTML = """<html><body><ul>
 <li itemtype="https://schema.org/Offer" itemprop="offers">
   <meta itemprop="price" content="72.50"><meta itemprop="category" content="Audio">
   <span itemprop="name">JBL Flip 7</span></li>
 <li itemtype="https://schema.org/Offer" itemprop="offers">
   <meta itemprop="price" content="71.00"><meta itemprop="category" content="Audio">
   <span itemprop="name">JBL Flip 7</span></li>
 <li itemtype="https://schema.org/Offer" itemprop="offers">
   <meta itemprop="price" content="18.00"><meta itemprop="category" content="Baterijas">
   <span itemprop="name">Baterija JBL Flip</span></li>
</ul></body></html>"""


def test_ceno_candidates_from_microdata():
    from stores.ceno import CENO
    cands = CENO.candidates(CENO_HTML, limit=3)
    assert cands == [{"name": "JBL Flip 7", "min_price": 71.0, "count": 2}]


def test_base_group_rows_ranking():
    rows = [("A", 10.0), ("A", 9.0), ("B", 5.0), ("C", 7.0)]
    out = StoreAdapter._group_rows(rows, limit=2)
    assert [c["name"] for c in out] == ["A", "B"]  # count first, then min price
    assert out[0]["min_price"] == 9.0
    assert StoreAdapter(domain="x", label="x", strategy="requests").candidates("") == []
