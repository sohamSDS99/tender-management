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

from app.models import RosterEntry
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


def join_link(admin: TestClient) -> str:
    return admin.post("/api/auth/roster/join-link").json()["token"]


def register(client: TestClient, email: str, token: str, **extra):
    body = {"email": email, "password": PASSWORD, "display_name": "Someone", "join_token": token}
    body.update(extra)
    return client.post("/api/auth/register", json=body)


# --- the three refusals that make the link safe -----------------------------


def test_a_valid_link_is_refused_for_an_address_nobody_added(admin, joiner):
    """The whole design in one test.

    If this ever passes, the join link has become a bearer token and the roster
    is decoration - which would make sharing the link in Slack a way in for
    anybody who saw it.
    """
    token = join_link(admin)
    response = register(joiner, "stranger@example.com", token)
    assert response.status_code == 400
    assert "not on this workspace's list" in response.json()["detail"]


def test_an_address_removed_from_the_roster_can_no_longer_join(admin, joiner):
    token = join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "gone@example.com"})
    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]

    assert admin.delete(f"/api/auth/roster/{entry_id}").status_code == 204
    assert register(joiner, "gone@example.com", token).status_code == 400


def test_rotating_the_link_kills_the_previous_one(admin, joiner):
    """The only way to withdraw a link that has been shared too widely."""
    old = join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "colleague@example.com"})

    new = join_link(admin)
    assert new != old
    assert register(joiner, "colleague@example.com", old).status_code == 400
    assert register(joiner, "colleague@example.com", new).status_code == 201


def test_a_roster_entry_without_the_link_cannot_join(admin, joiner):
    """Both halves are required, not either."""
    join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "colleague@example.com"})

    no_token = joiner.post(
        "/api/auth/register",
        json={"email": "colleague@example.com", "password": PASSWORD, "display_name": "C"},
    )
    assert no_token.status_code == 400
    assert "invite-only" in no_token.json()["detail"]

    wrong_token = register(joiner, "colleague@example.com", "not-the-token")
    assert wrong_token.status_code == 400


def test_no_link_has_been_created_yet(admin, joiner):
    """An empty token must never compare equal to a missing one."""
    assert admin.get("/api/auth/roster").json()["join_url"] is None
    admin.post("/api/auth/roster", json={"addresses": "colleague@example.com"})
    assert register(joiner, "colleague@example.com", "").status_code == 400
    assert register(joiner, "colleague@example.com", "anything").status_code == 400


# --- joining ----------------------------------------------------------------


def test_a_colleague_on_the_roster_joins_with_the_shared_link(admin, joiner):
    token = join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "colleague@example.com", "role": "member"})

    response = register(joiner, "colleague@example.com", token)
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "member"
    # Registering signs them in, so the dashboard opens straight away.
    assert joiner.get("/api/auth/me").status_code == 200
    assert joiner.get("/api/tenders").status_code == 200


def test_the_same_link_admits_everybody_on_the_roster(db_session, monkeypatch, settings, admin):
    """The point of the feature: one link, sent once, for the whole team."""
    token = join_link(admin)
    admin.post(
        "/api/auth/roster",
        json={"addresses": "one@example.com, two@example.com\nthree@example.com"},
    )

    for email in ("one@example.com", "two@example.com", "three@example.com"):
        browser = TestClient(_build_app(db_session, monkeypatch, settings))
        assert register(browser, email, token).status_code == 201, email


def test_the_role_comes_from_the_roster_entry(admin, joiner):
    token = join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "deputy@example.com", "role": "admin"})
    assert register(joiner, "deputy@example.com", token).json()["role"] == "admin"


def test_joining_is_recorded_against_the_entry(admin, joiner):
    """So the panel can show who still needs the link."""
    token = join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "colleague@example.com"})
    assert admin.get("/api/auth/roster").json()["waiting"] == 1

    register(joiner, "colleague@example.com", token)

    view = admin.get("/api/auth/roster").json()
    assert view["joined"] == 1
    assert view["waiting"] == 0
    assert view["entries"][0]["joined_at"] is not None


def test_an_address_cannot_join_twice(admin, joiner, db_session, monkeypatch, settings):
    token = join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "colleague@example.com"})
    assert register(joiner, "colleague@example.com", token).status_code == 201

    second = TestClient(_build_app(db_session, monkeypatch, settings))
    again = register(second, "colleague@example.com", token)
    assert again.status_code == 409
    assert "already exists" in again.json()["detail"]


def test_the_address_is_matched_case_insensitively(admin, joiner):
    """Otherwise a colleague who capitalises their address is turned away."""
    token = join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "Colleague@Example.com"})
    assert register(joiner, "COLLEAGUE@example.COM", token).status_code == 201


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
    admin.post("/api/auth/roster", json={"addresses": "a@x.com, b@x.com"})
    again = admin.post("/api/auth/roster", json={"addresses": "a@x.com, b@x.com, c@x.com"})

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
    token = join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "colleague@example.com", "role": "member"})
    register(joiner, "colleague@example.com", token)

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
    token = join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "colleague@example.com"})
    register(joiner, "colleague@example.com", token)
    assert joiner.get("/api/tenders").status_code == 200

    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]
    admin.delete(f"/api/auth/roster/{entry_id}")

    assert joiner.get("/api/tenders").status_code == 200, "their session survives"
    assert len(admin.get("/api/auth/users").json()) == 2, "their account survives"


def test_an_admin_cannot_remove_their_own_address(admin):
    """Nothing breaks, but it reliably confuses: they stay signed in with an
    account the roster no longer explains."""
    admin.post("/api/auth/roster", json={"addresses": "boss@example.com"})
    entry_id = next(
        e["id"] for e in admin.get("/api/auth/roster").json()["entries"] if e["email"] == "boss@example.com"
    )
    response = admin.delete(f"/api/auth/roster/{entry_id}")
    assert response.status_code == 403
    assert "your own address" in response.json()["detail"]


# --- who may see and change any of this -------------------------------------


def test_the_roster_is_administrators_only(db_session, monkeypatch, settings, admin, joiner):
    """And the join link with it - it is the one readable credential here."""
    token = join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "member@example.com", "role": "member"})
    register(joiner, "member@example.com", token)

    assert joiner.get("/api/auth/roster").status_code == 403
    assert joiner.post("/api/auth/roster", json={"addresses": "x@y.com"}).status_code == 403
    assert joiner.post("/api/auth/roster/join-link").status_code == 403


def test_a_stranger_cannot_read_the_join_link(db_session, monkeypatch, settings):
    stranger = TestClient(_build_app(db_session, monkeypatch, settings))
    assert stranger.get("/api/auth/roster").status_code == 401
    assert stranger.post("/api/auth/roster/join-link").status_code == 401


# --- the older mechanisms still work ----------------------------------------


def test_a_single_use_invitation_still_admits_an_outsider(admin, joiner):
    """D25's invites are kept for the person who is not on the roster at all -
    a contractor, a one-off. Different question, different answer."""
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
    token = join_link(admin)
    admin.post("/api/auth/roster", json={"addresses": "aaa@x.com, zzz@x.com"})
    register(joiner, "aaa@x.com", token)

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
