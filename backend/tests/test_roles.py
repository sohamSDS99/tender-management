"""Who may change a role — and it is administrators, nobody else (D30).

The rule is one sentence: **only an administrator can change what somebody is.**
It was already true of the two endpoints that say ``role`` out loud, and it had
no test of its own — which is the same gap the sign-in gate shipped with. A
refactor that dropped ``require_admin`` from one decorator would have gone
through green, and the failure mode is not a broken page, it is a member
promoting themselves.

So this file is deliberately a file of refusals, and it enumerates *every* way a
role can be written rather than the two obvious ones:

* ``PATCH /api/auth/users/{id}`` — an account's role, the real one
* ``PATCH /api/auth/roster/{id}`` — the role an address will get when it joins
* ``POST /api/auth/roster`` — the same thing, at the moment an address is added
* ``PATCH /api/auth/me`` — the self-service door, which must not accept a role
* ``POST /api/auth/register`` — the anonymous door, same
* ``POST /api/auth/accept`` — re-opening a link, which must not re-role anybody

The last three are the interesting ones. They are not administrative endpoints
at all, so they have no ``require_admin`` to lose; what protects them is that
they never read a role from the caller. That is a property of their *shape*, and
a property is exactly the kind of thing that gets refactored away by accident.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import ROLE_ADMIN, ROLE_MEMBER, User
from tests.conftest import TEST_PASSWORD, _build_app, make_account


@pytest.fixture
def app(db_session, monkeypatch, settings):
    return _build_app(db_session, monkeypatch, settings)


#: The two people in this file, named so a test reads as a sentence.
BOSS = "boss@example.com"
COLLEAGUE = "colleague@example.com"


@pytest.fixture
def admin(app, db_session, settings):
    """A signed-in administrator: the only caller allowed to move anybody."""
    _user, token = make_account(db_session, settings, email=BOSS, role=ROLE_ADMIN)
    return TestClient(app, cookies={settings.session_cookie_name: token})


@pytest.fixture
def member(app, db_session, settings):
    """A signed-in member. Everything in this file is about what they cannot do."""
    _user, token = make_account(db_session, settings, email=COLLEAGUE, role=ROLE_MEMBER)
    return TestClient(app, cookies={settings.session_cookie_name: token})


@pytest.fixture
def stranger(app):
    """No cookie at all, as the public internet arrives."""
    return TestClient(app)


def id_of(db_session, email: str) -> int:
    """Their account id, read from the database.

    Read here rather than smuggled onto the fixture's client object: a test
    client that carries a `user_id` attribute is a client pretending to be a
    person, and the next reader has to work out where that field came from.
    """
    return db_session.query(User).filter(User.email == email).one().id


def role_of(db_session, email: str) -> str:
    """Read the role from the database, not from a response body.

    The point of these tests is that nothing *changed*, and a 403 with a
    silently applied write behind it is precisely the bug worth catching.
    """
    return db_session.query(User).filter(User.email == email).one().role


# --- an account's role ------------------------------------------------------


def test_a_member_cannot_promote_anybody(member, admin, db_session):
    response = member.patch(f"/api/auth/users/{id_of(db_session, BOSS)}", json={"role": "member"})
    assert response.status_code == 403
    assert role_of(db_session, BOSS) == ROLE_ADMIN


def test_a_member_cannot_promote_themselves(member, db_session):
    """The one everybody tries. 403, and the row is untouched."""
    response = member.patch(f"/api/auth/users/{id_of(db_session, COLLEAGUE)}", json={"role": "admin"})
    assert response.status_code == 403
    assert role_of(db_session, COLLEAGUE) == ROLE_MEMBER


def test_a_member_cannot_deactivate_anybody(member, admin, db_session):
    """Same endpoint, the other field. Both halves need the same gate."""
    response = member.patch(f"/api/auth/users/{id_of(db_session, BOSS)}", json={"is_active": False})
    assert response.status_code == 403
    assert db_session.query(User).filter(User.email == BOSS).one().is_active


def test_a_member_cannot_even_read_who_is_here(member):
    """The list is the input to the change, and it names everyone's role."""
    assert member.get("/api/auth/users").status_code == 403


def test_a_stranger_is_asked_to_identify_themselves_first(stranger, admin, db_session):
    """401, not 403: the two are different answers and must not collapse.

    401 means "say who you are", 403 means "you have, and it is not enough". A
    403 here would have the dashboard show a *sign-in form* to somebody already
    signed in, and a 401 to a member would have it forget a perfectly good
    session.
    """
    assert stranger.get("/api/auth/users").status_code == 401
    assert (
        stranger.patch(f"/api/auth/users/{id_of(db_session, BOSS)}", json={"role": "member"}).status_code
        == 401
    )
    assert role_of(db_session, BOSS) == ROLE_ADMIN


def test_an_administrator_can_change_a_role(admin, member, db_session):
    """The permission has to actually work, or the gate is just a wall."""
    response = admin.patch(f"/api/auth/users/{id_of(db_session, COLLEAGUE)}", json={"role": "admin"})
    assert response.status_code == 200
    assert response.json()["role"] == ROLE_ADMIN
    assert role_of(db_session, COLLEAGUE) == ROLE_ADMIN


def test_the_last_administrator_cannot_be_demoted_even_by_themselves(admin, db_session):
    """Being allowed to change roles is not being allowed to strand the deployment."""
    response = admin.patch(f"/api/auth/users/{id_of(db_session, BOSS)}", json={"role": "member"})
    assert response.status_code == 403
    assert role_of(db_session, BOSS) == ROLE_ADMIN


# --- the role an address will get when it joins -----------------------------


def test_a_member_cannot_change_the_role_waiting_on_the_roster(admin, member, db_session):
    """A roster role is a role. Leaving this door open would be the same hole
    one step earlier: a member sets an entry to admin, opens that link, and is
    an administrator without anybody having promoted anyone."""
    admin.post("/api/auth/roster", json={"addresses": "later@example.com", "role": "member"})
    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]

    assert member.patch(f"/api/auth/roster/{entry_id}", json={"role": "admin"}).status_code == 403
    assert admin.get("/api/auth/roster").json()["entries"][0]["role"] == ROLE_MEMBER


def test_a_member_cannot_add_an_administrator_to_the_roster(member, admin):
    assert (
        member.post("/api/auth/roster", json={"addresses": "friend@example.com", "role": "admin"}).status_code
        == 403
    )
    assert admin.get("/api/auth/roster").json()["total"] == 0


def test_a_member_cannot_mint_or_revoke_a_link(admin, member):
    """A link carries a role, so issuing one is a role decision too."""
    admin.post("/api/auth/roster", json={"addresses": "later@example.com", "role": "admin"})
    entry_id = admin.get("/api/auth/roster").json()["entries"][0]["id"]

    assert member.post(f"/api/auth/roster/{entry_id}/link").status_code == 403
    assert member.delete(f"/api/auth/roster/{entry_id}/link").status_code == 403
    assert member.delete(f"/api/auth/roster/{entry_id}").status_code == 403


# --- the doors that have no administrator gate to lose ----------------------


def test_the_profile_endpoint_will_not_take_a_role(member, db_session):
    """``PATCH /api/auth/me`` is the one write a member owns, and it owns two
    fields. A role sent alongside them is ignored rather than honoured — and it
    is ignored because ``ProfileUpdate`` has no such field, not because anything
    checks for it. That is the property worth pinning."""
    response = member.patch("/api/auth/me", json={"display_name": "Renamed", "role": "admin"})
    assert response.status_code == 200
    assert response.json()["role"] == ROLE_MEMBER
    assert response.json()["display_name"] == "Renamed"
    assert role_of(db_session, COLLEAGUE) == ROLE_MEMBER


def test_registering_cannot_ask_for_a_role(stranger, db_session, settings):
    """The bootstrap account is an administrator because it is *first*, never
    because it asked. On an empty deployment that is the same answer either way,
    so the test that matters is the second account: an invited registration that
    requests ``admin`` gets whatever the invitation says."""
    from app.services import accounts

    boss, _ = make_account(db_session, settings, email=BOSS, role=ROLE_ADMIN)
    token, _invite = accounts.create_invite(
        db_session, boss, email="outsider@example.com", role=ROLE_MEMBER, note="", settings=settings
    )

    response = stranger.post(
        "/api/auth/register",
        json={
            "email": "outsider@example.com",
            "password": TEST_PASSWORD,
            "display_name": "Outsider",
            "invite_token": token,
            "role": "admin",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == ROLE_MEMBER
    assert role_of(db_session, "outsider@example.com") == ROLE_MEMBER


def test_re_opening_a_link_never_re_roles_an_existing_account(admin, stranger, db_session):
    """The subtle one, and the reason the roster and the account are separate.

    An administrator re-roles a roster entry to ``admin``; the person it belongs
    to already has an account. Opening their link again signs them in — it must
    not read the entry's role a second time and promote them. If it did, "roster
    edits do not touch existing accounts" would be true only until the person
    next opened their own link, which is a promotion nobody performed.
    """
    admin.post("/api/auth/roster", json={"addresses": "joiner@example.com", "role": "member"})
    entry = admin.get("/api/auth/roster").json()["entries"][0]
    link_token = entry["access_url"].split("accept=")[1]

    assert stranger.post("/api/auth/accept", json={"token": link_token}).json()["role"] == ROLE_MEMBER

    admin.patch(f"/api/auth/roster/{entry['id']}", json={"role": "admin"})
    again = stranger.post("/api/auth/accept", json={"token": link_token})

    assert again.status_code == 200, "their link still signs them in"
    assert again.json()["role"] == ROLE_MEMBER, "and it does not promote them"
    assert role_of(db_session, "joiner@example.com") == ROLE_MEMBER
