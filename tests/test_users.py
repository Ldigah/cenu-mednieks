import users


def U(ud, cid):
    u = users.get(ud, cid)
    assert u is not None
    return u



def mk(ud, chat_id=2, name="Friend", role="member", status="active"):
    u = users.new_user(chat_id, name, "", role, status)
    ud.setdefault("users", []).append(u)
    return u


def test_ensure_and_migrate_owner():
    ud = users.ensure(None)
    assert ud["users"] == []
    users.migrate_owner(ud, 111, "Leon", "ldigah")
    o = users.owner(ud)
    assert o is not None
    assert o["role"] == users.ROLE_OWNER and o["status"] == users.ST_ACTIVE
    assert o["watch_all"] is True and o["prefs"]["digest"] is True
    # second call is a no-op
    users.migrate_owner(ud, 222)
    o2 = users.owner(ud)
    assert o2 is not None and o2["chat_id"] == 111


def test_request_approve_decline_flow():
    ud = users.ensure(None)
    mk(ud, chat_id=1, role="owner")
    assert users.request_access(ud, 42, "Ada", "ada") is True
    assert users.request_access(ud, 42, "Ada", "ada") is False  # no duplicate
    assert len(users.pending(ud)) == 1
    u = users.approve(ud, 42)
    assert u is not None
    assert u["status"] == users.ST_ACTIVE
    assert users.is_member(ud, 42) and not users.is_owner(ud, 42)
    assert users.decline(ud, 42) is None  # nothing pending anymore
    assert users.approve(ud, 999) is None  # unknown user
    # blocked users can't silently re-request
    u["status"] = users.ST_BLOCKED
    assert users.request_access(ud, 42) is False


def test_wants_alert_watchlist_and_mute():
    ud = users.ensure(None)
    owner = mk(ud, chat_id=1, role="owner")
    member = mk(ud, chat_id=2)
    assert users.wants_alert(owner, "jbl-flip-7")          # watch_all
    assert not users.wants_alert(member, "jbl-flip-7")     # silent until they watch
    users.watch(member, "jbl-flip-7")
    assert users.wants_alert(member, "jbl-flip-7")
    users.mute(member, "jbl-flip-7")
    assert not users.wants_alert(member, "jbl-flip-7")     # mute beats watch
    users.unmute(member, "jbl-flip-7")
    assert users.wants_alert(member, "jbl-flip-7")
    users.unwatch(member, "jbl-flip-7")
    assert not users.wants_alert(member, "jbl-flip-7")
    assert not users.wants_alert({}, "jbl-flip-7")  # defensive


def test_effective_ids_and_muted():
    ud = users.ensure(None)
    owner = mk(ud, chat_id=1, role="owner")
    member = mk(ud, chat_id=2)
    users.watch(member, "a")
    users.watch(member, "b")
    users.mute(member, "b")
    ids = ["a", "b", "c"]
    assert users.effective_ids(member, ids) == ["a"]
    assert users.effective_ids(owner, ids) == ["a", "b", "c"]
    users.mute(owner, "c")
    assert users.effective_ids(owner, ids) == ["a", "b"]


def test_quota_owner_exempt_member_capped():
    ud = users.ensure(None)
    owner = mk(ud, chat_id=1, role="owner")
    member = mk(ud, chat_id=2)
    products = {"products": [{"id": f"p{i}", "added_by": 2} for i in range(10)]}
    assert users.quota_left(owner, products) is None
    assert users.quota_left(member, products) == 0
    member["quota"] = 12
    assert users.quota_left(member, products) == 2
    # products without added_by count for nobody
    assert users.products_added_by(products, 1) == 0


def test_set_pref_validation():
    u = users.new_user(1)
    assert users.set_pref(u, "digest", False)
    assert users.set_pref(u, "digest_hour_utc", 13)
    assert not users.set_pref(u, "digest_hour_utc", 4)   # only run hours exist
    assert users.set_pref(u, "quiet", "23-8")
    assert not users.set_pref(u, "quiet", "1-5")
    assert users.set_pref(u, "lang", "lv")
    assert not users.set_pref(u, "lang", "de")
    assert not users.set_pref(u, "bogus", 1)


def test_quiet_hours_wrap_midnight():
    from datetime import UTC, datetime
    u = users.new_user(1)
    assert not users.is_quiet_now(u, datetime(2026, 11, 19, 23, 30, tzinfo=UTC))
    users.set_pref(u, "quiet", "23-8")
    assert users.is_quiet_now(u, datetime(2026, 11, 19, 23, 30, tzinfo=UTC))
    assert users.is_quiet_now(u, datetime(2026, 11, 19, 3, 0, tzinfo=UTC))
    assert not users.is_quiet_now(u, datetime(2026, 11, 19, 12, 0, tzinfo=UTC))
    assert not users.is_quiet_now(u, datetime(2026, 11, 19, 8, 0, tzinfo=UTC))  # end exclusive


def test_merge_users_union_and_record_wins():
    theirs = users.ensure({"users": [
        {"chat_id": 1, "name": "Old", "role": "owner", "status": "active",
         "watchlist": ["x"]}]})
    ours = users.ensure({"users": [
        {"chat_id": 1, "name": "New", "role": "owner", "status": "active",
         "watchlist": ["y"]},
        {"chat_id": 5, "name": "AddedElsewhere", "role": "member", "status": "active"}]})
    out = users.merge_users(theirs, ours)
    by_id = {u["chat_id"]: u for u in out["users"]}
    assert by_id[1]["name"] == "New"          # ours wins per record
    assert by_id[1]["watchlist"] == ["y"]
    assert by_id[5]["name"] == "AddedElsewhere"  # union: user added by another runner kept
    assert out["version"] == users.USERS_VERSION
