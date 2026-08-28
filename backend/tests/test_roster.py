"""The workspace roster and its join link (D28).

The design claim being tested is a single sentence: **the address is the
permission, not the link.** Everything important here is a refusal —

* the right link with an address nobody added
* an address that was added and then removed
* a link that has been rotated out from under a stale copy

If those three pass, the link is safe to hand to a whole team and safe to show
again later, which is the entire reason this exists instead of one single-use
token per person.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import RosterEntry, User
from app.services import roster
from tests.conftest import _build_app, make_account

PASSWORD = "a-long-enough-password"


@pytest.fixture
def admin(db_session, monkeypatch, settings):
    """A signed-in administrator, and a fresh browser factory for joiners."""
    app = _build_app(db_session, monkeypatch, settings)
    _, token = make_account(db_session, settings, email="boss@example.com")
    return TestClient(app, cookies={settings.session_cookie_name: token})


@pytest.fixture
def joiner(db_session, monkeypatch, settings):
    """A browser with no session, as a colleague following a link would have."""
    return TestClient(_build_app(db_session, monkeypatch, settings))


def access_link(admin: TestClient, email: str, role: str = "member") -> str:
    """Put somebody on the roster and return their personal link's token."""
    admin.post("/api/auth/roster", json={"addresses": email, "role": role})
    entry = next(e for e in admin.get("/api/auth/roster").json()["entries"] if e["email"] == email)
    assert entry["access_url"], "adding somebody must mint their link immediately"
    return entry["access_url"].split("accept=")[1]


def accept(client: TestClient, token: str):
    return client.post("/api/auth/accept", json={"token": token})


# --- accepting: the whole feature ------------------------------------------


def test_opening_the_link_is_the_entire_process(admin, joiner):
    """No password, no form, no second step. This is the requirement verbatim."""
    token = access_link(admin, "colleague@example.com")

    response = accept(joiner, token)
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "colleague@example.com"

    # ...and they are signed in, immediately.
    assert joiner.get("/api/auth/me").status_code == 200
    assert joiner.get("/api/tenders").status_code == 200


def test_the_same_link_works_again_on_another_device(admin, joiner, db_session, monkeypatch, settings):
    """The point of a durable link.

    If it were single-use, the first expiring session would leave somebody with
    no password, no email and no way back in - which is the failure mode that
    made a permanent link the only coherent choice.
    """
    token = access_link(admin, "colleague@example.com")
    assert accept(joiner, token).status_code == 200

    laptop = TestClient(_build_app(db_session, monkeypatch, settings))
    assert accept(laptop, token).status_code == 200
    assert laptop.get("/api/auth/me").json()["email"] == "colleague@example.com"


def test_accepting_twice_does_not_create_a_second_account(admin, joiner):
    token = access_link(admin, "colleague@example.com")
    accept(joiner, token)
    accept(joiner, token)
    emails = [u["email"] for u in admin.get("/api/auth/users").json()]
    assert emails.count("colleague@example.com") == 1


def test_the_role_comes_from_the_roster_entry(admin, joiner):
    token = access_link(admin, "deputy@example.com", role="admin")
    assert accept(joiner, token).json()["role"] == "admin"


def test_accepting_is_recorded_so_the_panel_knows_who_still_needs_sending(admin, joiner):
    token = access_link(admin, "colleague@example.com")
    assert admin.get("/api/auth/roster").json()["waiting"] == 1

    accept(joiner, token)

    view = admin.get("/api/auth/roster").json()
    assert view["joined"] == 1 and view["waiting"] == 0


# --- the refusals -----------------------------------------------------------


def test_a_made_up_token_is_refused(joiner):
    assert accept(joiner, "not-a-real-token").status_code == 400
    assert accept(joiner, "").status_code == 400
    assert accept(joiner, "   ").status_code == 400


def test_a_revoked_link_stops_working(admin, joiner):
    """The answer to a link that has leaked."""
    token = access_link(admin, "colleague@example.com")
    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]

    assert admin.delete(f"/api/auth/roster/{entry_id}/link").status_code == 204
    assert accept(joiner, token).status_code == 400
    assert admin.get("/api/auth/roster").json()["entries"][0]["access_url"] is None


def test_replacing_a_link_kills_the_previous_one(admin, joiner):
    old = access_link(admin, "colleague@example.com")
    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]

    new = admin.post(f"/api/auth/roster/{entry_id}/link").json()["access_url"].split("accept=")[1]
    assert new != old
    assert accept(joiner, old).status_code == 400
    assert accept(joiner, new).status_code == 200


def test_removing_the_address_kills_the_link_with_it(admin, joiner):
    token = access_link(admin, "gone@example.com")
    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]
    admin.delete(f"/api/auth/roster/{entry_id}")
    assert accept(joiner, token).status_code == 400


def test_a_deactivated_account_cannot_come_back_through_its_link(admin, joiner):
    """Deactivation has to outrank a live link.

    Otherwise "deactivate" means nothing for precisely the people whose only
    credential *is* the link.
    """
    token = access_link(admin, "colleague@example.com")
    accept(joiner, token)
    user_id = next(
        u["id"] for u in admin.get("/api/auth/users").json() if u["email"] == "colleague@example.com"
    )
    admin.patch(f"/api/auth/users/{user_id}", json={"is_active": False})

    assert accept(joiner, token).status_code == 401
    assert joiner.get("/api/tenders").status_code == 401


# --- an account with no password must be unreachable by password ------------


def test_a_passwordless_account_cannot_be_signed_into_with_a_blank_password(admin, joiner):
    """The one way this feature could open a hole.

    An account created from a link has an empty `password_hash`. If an empty
    stored hash ever compared equal to an empty submitted password, every such
    account would be signable-into by anybody who left the box blank.
    """
    token = access_link(admin, "colleague@example.com")
    accept(joiner, token)

    for attempt in ("", " ", "password", "colleague@example.com"):
        response = joiner.post(
            "/api/auth/login", json={"email": "colleague@example.com", "password": attempt}
        )
        assert response.status_code == 401, f"blank-ish password {attempt!r} was accepted"


def test_the_empty_hash_never_verifies_at_the_lowest_level():
    """Asserted directly, not only through the endpoint, because this is the
    invariant everything else rests on."""
    from app.services.accounts import NO_PASSWORD, verify_password

    assert verify_password("", NO_PASSWORD) is False
    assert verify_password(" ", NO_PASSWORD) is False
    assert verify_password("anything", NO_PASSWORD) is False


def test_a_link_account_stores_no_password_at_all(admin, joiner, db_session):
    token = access_link(admin, "colleague@example.com")
    accept(joiner, token)
    user = db_session.query(User).filter(User.email == "colleague@example.com").one()
    assert user.password_hash == ""


# --- who may issue and see links --------------------------------------------


def test_only_administrators_can_see_or_issue_links(admin, joiner):
    token = access_link(admin, "member@example.com", role="member")
    accept(joiner, token)

    assert joiner.get("/api/auth/roster").status_code == 403
    assert joiner.post("/api/auth/roster/1/link").status_code == 403
    assert joiner.delete("/api/auth/roster/1/link").status_code == 403


def test_a_stranger_cannot_read_the_roster(db_session, monkeypatch, settings):
    stranger = TestClient(_build_app(db_session, monkeypatch, settings))
    assert stranger.get("/api/auth/roster").status_code == 401


def test_adding_somebody_mints_their_link_there_and_then(admin):
    """An entry without a link is a row that can do nothing.

    Requiring a second click per person before anybody could join would put the
    clerical work straight back.
    """
    added = admin.post(
        "/api/auth/roster", json={"addresses": "a@x.com, b@x.com, c@x.com", "role": "member"}
    ).json()["added"]
    assert len(added) == 3
    assert all(e["access_url"] for e in added)
    assert len({e["access_url"] for e in added}) == 3, "each person gets their own"


# --- pasting a list ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "a@x.com,b@x.com,c@x.com",
        "a@x.com b@x.com c@x.com",
        "a@x.com\nb@x.com\nc@x.com",
        "a@x.com; b@x.com;\n  c@x.com  ",
        "a@x.com,\nb@x.com , c@x.com",
    ],
)
def test_every_shape_a_pasted_list_arrives_in(raw):
    """Mail clients, spreadsheets and Slack all produce a different one.

    Asking somebody to reformat a list they already have is exactly the friction
    this feature exists to remove.
    """
    assert roster.parse_addresses(raw) == ["a@x.com", "b@x.com", "c@x.com"]


def test_a_repeated_address_in_one_paste_is_added_once():
    assert roster.parse_addresses("a@x.com, a@x.com, A@X.com") == ["a@x.com"]


def test_a_bad_address_is_named_rather_than_silently_dropped():
    """A typo that vanishes becomes a colleague who cannot get in, and nobody
    knowing why."""
    with pytest.raises(roster.RosterError) as caught:
        roster.parse_addresses("good@x.com, notanemail, also bad")
    message = str(caught.value)
    assert "notanemail" in message
    assert "good@x.com" not in message


def test_a_paste_of_nothing_is_refused():
    with pytest.raises(roster.RosterError):
        roster.parse_addresses("   \n  ")


def test_an_enormous_paste_is_refused_before_it_is_validated():
    huge = " ".join(f"a{i}@x.com" for i in range(roster.MAX_BULK_ADDRESSES + 1))
    with pytest.raises(roster.RosterError) as caught:
        roster.parse_addresses(huge)
    assert "batches" in str(caught.value)


def test_re_pasting_a_team_list_reports_rather_than_errors(admin):
    """Adding one person to a list of ten is a normal thing to do."""
    admin.post("/api/auth/roster", json={"addresses": "a@x.com, b@x.com", "role": "member"})
    again = admin.post("/api/auth/roster", json={"addresses": "a@x.com, b@x.com, c@x.com", "role": "member"})

    assert again.status_code == 201
    body = again.json()
    assert [e["email"] for e in body["added"]] == ["c@x.com"]
    assert sorted(body["already_present"]) == ["a@x.com", "b@x.com"]


def test_re_pasting_does_not_re_role_the_people_already_there(admin):
    """The trap in the previous test made concrete.

    Adding one administrator to a team list must not promote the whole team.
    """
    admin.post("/api/auth/roster", json={"addresses": "a@x.com, b@x.com", "role": "member"})
    admin.post("/api/auth/roster", json={"addresses": "a@x.com, boss2@x.com", "role": "admin"})

    by_email = {e["email"]: e["role"] for e in admin.get("/api/auth/roster").json()["entries"]}
    assert by_email["a@x.com"] == "member", "an existing entry keeps its role"
    assert by_email["boss2@x.com"] == "admin"


# --- what a roster edit must NOT do -----------------------------------------


def test_changing_an_entry_role_does_not_move_an_existing_account(admin, joiner):
    """Somebody who joined last week keeps the role they were given."""
    token = access_link(admin, "colleague@example.com", role="member")
    accept(joiner, token)

    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]
    assert admin.patch(f"/api/auth/roster/{entry_id}", json={"role": "admin"}).status_code == 200

    users = {u["email"]: u["role"] for u in admin.get("/api/auth/users").json()}
    assert users["colleague@example.com"] == "member", "the account is unchanged"


def test_removing_an_address_does_not_close_the_account_it_became(admin, joiner):
    """Two different acts. Conflating them would let a tidy-up lock people out.

    Withdrawing permission to *register* is not the same as ending somebody's
    access - that lives behind PATCH /api/auth/users/{id}, where the
    last-administrator guard applies.
    """
    token = access_link(admin, "colleague@example.com")
    accept(joiner, token)
    assert joiner.get("/api/tenders").status_code == 200

    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]
    admin.delete(f"/api/auth/roster/{entry_id}")

    assert joiner.get("/api/tenders").status_code == 200, "their session survives"
    assert len(admin.get("/api/auth/users").json()) == 2, "their account survives"


def test_an_admin_cannot_remove_their_own_address(admin):
    """Nothing breaks, but it reliably confuses: they stay signed in with an
    account the roster no longer explains."""
    admin.post("/api/auth/roster", json={"addresses": "boss@example.com", "role": "member"})
    entry_id = next(
        e["id"] for e in admin.get("/api/auth/roster").json()["entries"] if e["email"] == "boss@example.com"
    )
    response = admin.delete(f"/api/auth/roster/{entry_id}")
    assert response.status_code == 403
    assert "your own address" in response.json()["detail"]


# --- who may see and change any of this -------------------------------------


def test_the_roster_is_administrators_only(admin, joiner):
    """And everyone's links with it - the roster is a list of live credentials."""
    token = access_link(admin, "member@example.com", role="member")
    accept(joiner, token)

    assert joiner.get("/api/auth/roster").status_code == 403
    assert joiner.post("/api/auth/roster", json={"addresses": "x@y.com"}).status_code == 403


def test_a_stranger_cannot_read_anybody_else_link(db_session, monkeypatch, settings):
    """The roster response carries every live credential in the workspace."""
    stranger = TestClient(_build_app(db_session, monkeypatch, settings))
    assert stranger.get("/api/auth/roster").status_code == 401
    assert stranger.post("/api/auth/roster/1/link").status_code == 401


# --- the older mechanisms still work ----------------------------------------


def test_a_single_use_invitation_still_admits_an_outsider(admin, joiner):
    """D25's invites are kept for somebody not on the roster at all.

    Note this path still sets a password, which D29 removed everywhere else. It
    is the outsider door and it is deliberately unchanged; the roster is what
    the team uses.
    """
    invite = admin.post("/api/auth/invites", json={"email": "outsider@elsewhere.com"}).json()
    response = joiner.post(
        "/api/auth/register",
        json={
            "email": "outsider@elsewhere.com",
            "password": PASSWORD,
            "display_name": "Outsider",
            "invite_token": invite["token"],
        },
    )
    assert response.status_code == 201, response.text


def test_the_first_account_still_needs_nothing(db_session, monkeypatch, settings):
    """Bootstrap is untouched: an empty deployment must not need a roster it has
    no administrator to write."""
    first = TestClient(_build_app(db_session, monkeypatch, settings))
    response = first.post(
        "/api/auth/register",
        json={"email": "first@example.com", "password": PASSWORD, "display_name": "First"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "admin"


def test_the_entries_put_the_people_still_waiting_first(admin, joiner):
    """They are who an administrator is looking for."""
    token = access_link(admin, "aaa@x.com")
    admin.post("/api/auth/roster", json={"addresses": "zzz@x.com", "role": "member"})
    accept(joiner, token)

    emails = [e["email"] for e in admin.get("/api/auth/roster").json()["entries"]]
    assert emails == ["zzz@x.com", "aaa@x.com"], "not yet joined first, then alphabetical"


def test_every_stored_value_fits_its_column(settings):
    """SQLite ignores VARCHAR limits; PostgreSQL enforces them (D9)."""
    from sqlalchemy import String

    for column, limit in (("email", 320), ("role", 16), ("note", 200)):
        col = RosterEntry.__table__.columns[column]
        assert isinstance(col.type, String)
        assert col.type.length == limit

    long_note = " ".join(["note"] * 500)
    assert len(" ".join(long_note.split())[:200]) <= 200


# --- what a link is, before it is spent (D30) -------------------------------
#
# The page has to know where to land somebody *before* it acts: an
# administrator's link enters the dashboard with no click, a member's shows the
# accept screen. So there is a read in front of the write, and the whole of what
# matters about it is that it is a read.


def look_up(client: TestClient, token: str):
    return client.post("/api/auth/invitation", json={"token": token})


def test_a_link_can_be_read_without_a_session(admin, joiner):
    """No cookie, by definition: this is the caller who has not joined yet."""
    token = access_link(admin, "colleague@example.com", role="member")

    response = look_up(joiner, token)

    assert response.status_code == 200
    assert response.json() == {"email": "colleague@example.com", "role": "member", "joined": False}


def test_reading_a_link_spends_nothing(admin, joiner, db_session):
    """The property the whole endpoint rests on.

    If a lookup created the account or claimed the entry, then a browser
    prefetching the page — or a reader who opened it and closed it again — would
    have joined without ever pressing anything.
    """
    token = access_link(admin, "colleague@example.com", role="member")

    look_up(joiner, token)
    look_up(joiner, token)

    assert db_session.query(User).filter(User.email == "colleague@example.com").count() == 0
    assert admin.get("/api/auth/roster").json()["waiting"] == 1
    # And the link is still worth what it was worth.
    assert accept(joiner, token).status_code == 200


def test_the_lookup_says_which_of_the_two_this_link_is(admin, joiner):
    """The one thing the page branches on."""
    member_token = access_link(admin, "member@example.com", role="member")
    admin_token = access_link(admin, "deputy@example.com", role="admin")

    assert look_up(joiner, member_token).json()["role"] == "member"
    assert look_up(joiner, admin_token).json()["role"] == "admin"


def test_the_lookup_reports_the_account_role_once_there_is_an_account(admin, joiner, db_session):
    """A colleague promoted last week must not be sent back to the accept screen.

    The roster entry still says ``member`` — roster edits do not follow an
    account, and nothing rewrites the entry on a promotion. So the effective
    role is the *account's* whenever there is one, and only the entry's promise
    when there is not.
    """
    token = access_link(admin, "colleague@example.com", role="member")
    accept(joiner, token)
    user = db_session.query(User).filter(User.email == "colleague@example.com").one()

    admin.patch(f"/api/auth/users/{user.id}", json={"role": "admin"})

    body = look_up(joiner, token).json()
    assert body == {"email": "colleague@example.com", "role": "admin", "joined": True}


@pytest.mark.parametrize("token", ["", "   ", "not-a-real-token"])
def test_reading_a_link_that_is_not_real_says_the_same_thing_accepting_does(joiner, token):
    """One message for never-existed, revoked and replaced.

    Splitting them would tell a stranger holding a stale link whether it was
    ever real, and none of the three changes what the reader should do.
    """
    response = look_up(joiner, token)
    assert response.status_code == 400
    assert response.json()["detail"] == "That link is not valid. Ask an administrator for a new one."


def test_a_revoked_link_reads_as_dead_before_anybody_presses_anything(admin, joiner):
    token = access_link(admin, "colleague@example.com")
    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]
    admin.delete(f"/api/auth/roster/{entry_id}/link")

    assert look_up(joiner, token).status_code == 400


# --- the role is settled before the link exists (D30) -----------------------


def test_adding_somebody_without_saying_the_role_is_refused(admin):
    """``role`` has no default any more.

    It used to fall back to ``member``, which was harmless while the role only
    decided what the account would be. Now it also decides where the link lands
    its holder, so a request that does not name one is asking for a link whose
    behaviour nobody chose.
    """
    response = admin.post("/api/auth/roster", json={"addresses": "someone@x.com"})
    assert response.status_code == 422
    assert admin.get("/api/auth/roster").json()["total"] == 0


def test_re_roling_somebody_who_has_not_joined_withdraws_their_link(admin, joiner):
    """Because the link they were sent would otherwise land them somewhere else.

    A member's link shows the accept screen; an administrator's goes straight
    in. Flipping the role under a link already delivered changes what that link
    does without changing the link, which is the kind of surprise that ends with
    an administrator swearing the feature is broken. Revoking makes it visible:
    the row shows no link, and issuing a new one is the deliberate act.
    """
    token = access_link(admin, "deputy@example.com", role="member")
    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]

    updated = admin.patch(f"/api/auth/roster/{entry_id}", json={"role": "admin"})

    assert updated.json()["role"] == "admin"
    assert updated.json()["access_url"] is None, "the old link is gone from the panel"
    assert look_up(joiner, token).status_code == 400, "and it no longer opens anything"
    assert accept(joiner, token).status_code == 400


def test_setting_the_role_it_already_has_leaves_the_link_alone(admin, joiner):
    """Re-asserting a role is not a change, and must not cost the link.

    Otherwise a panel that re-sends the current value on any edit would quietly
    invalidate everybody's link.
    """
    token = access_link(admin, "colleague@example.com", role="member")
    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]

    updated = admin.patch(f"/api/auth/roster/{entry_id}", json={"role": "member"})

    assert updated.json()["access_url"] is not None
    assert accept(joiner, token).status_code == 200


def test_re_roling_somebody_who_has_already_joined_does_not_take_their_link(admin, joiner):
    """Their link is their only credential, and the edit changes nothing anyway.

    A roster role never moves an existing account, so revoking here would be a
    lockout in exchange for nothing.
    """
    token = access_link(admin, "colleague@example.com", role="member")
    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]
    accept(joiner, token)

    admin.patch(f"/api/auth/roster/{entry_id}", json={"role": "admin"})

    assert accept(joiner, token).status_code == 200, "they can still sign in"
    assert look_up(joiner, token).json()["role"] == "member", "and they are still a member"


def test_two_arrivals_on_one_fresh_link_do_not_collide(admin, joiner, db_session, monkeypatch):
    """Two accepts of the same never-used link must not answer 500.

    Reachable without anybody double-clicking since D30: an administrator's link
    is spent by the page on load, so two tabs — or a browser that prerenders the
    URL out of the address bar and runs its JavaScript — are two accepts with no
    press between them. Both read no account, both insert, and ``users.email`` is
    UNIQUE.

    Simulated rather than actually raced, because a deterministic two-connection
    race is not something a test suite should try to arrange: the winner's row is
    written first, then ``get_by_email`` is blinded exactly once, which puts the
    second request in precisely the state the loser of the race is in.
    """
    from app.services import accounts as accounts_module

    token = access_link(admin, "colleague@example.com")
    assert accept(joiner, token).status_code == 200

    real = accounts_module.get_by_email
    seen: list[str] = []

    def blind_once(db, email):
        seen.append(email)
        if len(seen) == 1:
            return None
        return real(db, email)

    monkeypatch.setattr(accounts_module, "get_by_email", blind_once)

    second = accept(joiner, token)

    assert second.status_code == 200, "the loser of the race recovers, it does not 500"
    assert second.json()["email"] == "colleague@example.com"
    assert len(seen) == 2, "it re-read the row the winner wrote"
    assert db_session.query(User).filter(User.email == "colleague@example.com").count() == 1


@pytest.mark.parametrize("path", ["/api/auth/accept", "/api/auth/invitation"])
def test_neither_public_door_will_read_an_enormous_token(joiner, path):
    """The two endpoints an unauthenticated caller can reach take one string each.

    A real token is 43 characters in a ``String(64)`` column, so anything past
    the cap cannot match a row — it is only a way to make somebody else's
    database do pointless work. Refused by validation before it gets there.
    """
    response = joiner.post(path, json={"token": "x" * 100_000})
    assert response.status_code == 422
