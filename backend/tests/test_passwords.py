"""Signing out must not be a lockout (D31).

The defect this file exists for was reported from use, not found by a test, and
the sequence is four steps with nothing exotic in it:

1. somebody opens their access link and lands in the dashboard (D29)
2. they press **Sign out**
3. the sign-in page asks for an email and a password
4. they have never had a password, so there is no answer they can give

Everything they could do about it was outside the product: find the original link
in a chat history, or get somebody with a shell on the host to run
``accounts_cli reset-password``. D29 called a permanent link "the only shape where
nothing else is needed", and that was true right up until the person signed out.

So the tests here are about the *second* credential — that one can exist, that
an administrator can grant it, that its owner can grant it to themselves, and
that granting it never quietly widens who can sign in as whom.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import ROLE_ADMIN, ROLE_MEMBER, User
from tests.conftest import TEST_PASSWORD, _build_app, make_account

NEW_PASSWORD = "a-brand-new-long-password"


@pytest.fixture
def app(db_session, monkeypatch, settings):
    return _build_app(db_session, monkeypatch, settings)


@pytest.fixture
def admin(app, db_session, settings):
    _user, token = make_account(db_session, settings, email="boss@example.com", role=ROLE_ADMIN)
    return TestClient(app, cookies={settings.session_cookie_name: token})


@pytest.fixture
def browser(app):
    """A browser with no session, as somebody arriving at the sign-in page has."""
    return TestClient(app)


def link_account(admin: TestClient, browser: TestClient, email: str, role: str = "member") -> int:
    """Put somebody on the roster, open their link, return their account id.

    The whole point is an account created *without* a password, which is the only
    way to reach the state this file is about.
    """
    admin.post("/api/auth/roster", json={"addresses": email, "role": role})
    entry = next(e for e in admin.get("/api/auth/roster").json()["entries"] if e["email"] == email)
    token = entry["access_url"].split("accept=")[1]
    accepted = browser.post("/api/auth/accept", json={"token": token})
    assert accepted.status_code == 200, accepted.text
    return int(accepted.json()["id"])


def sign_in(client: TestClient, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# --- the reported defect, and that it is now closed -------------------------


def test_a_link_account_starts_with_no_password_and_says_so(admin, browser):
    """`has_password` is the bit nobody could see before.

    It is the difference between "signing out logs me out" and "signing out locks
    me out", and the page cannot warn about a state it cannot read.
    """
    link_account(admin, browser, "colleague@example.com")

    assert browser.get("/api/auth/me").json()["has_password"] is False
    assert admin.get("/api/auth/users").json()[0]["has_password"] is True, "the boss has one"
    listed = next(u for u in admin.get("/api/auth/users").json() if u["email"].startswith("coll"))
    assert listed["has_password"] is False


def test_signing_out_used_to_be_a_lockout_and_now_is_not(admin, browser):
    """The reported sequence, end to end, with the fix in place.

    Join by link, set a password, sign out, sign back in. Step two is the new
    one; without it step four has no answer.
    """
    link_account(admin, browser, "colleague@example.com")

    set_it = browser.post("/api/auth/me/password", json={"new_password": NEW_PASSWORD})
    assert set_it.status_code == 200, set_it.text
    assert browser.get("/api/auth/me").json()["has_password"] is True

    assert browser.post("/api/auth/logout").status_code == 204
    assert browser.get("/api/auth/me").status_code == 401, "really signed out"

    back = sign_in(browser, "colleague@example.com", NEW_PASSWORD)
    assert back.status_code == 200, "and back in with the password they set"
    assert back.json()["email"] == "colleague@example.com"
    assert browser.get("/api/tenders").status_code == 200, "the dashboard is theirs again"


def test_setting_a_first_password_needs_no_old_one(admin, browser):
    """There is no old one, and asking for it locks out the only group that needs this."""
    link_account(admin, browser, "colleague@example.com")

    response = browser.post("/api/auth/me/password", json={"new_password": NEW_PASSWORD})

    assert response.status_code == 200
    assert response.json()["revoked"] == 0, "their own session survives setting it"
    assert browser.get("/api/auth/me").status_code == 200


def test_claiming_a_current_password_that_does_not_exist_is_refused(admin, browser):
    """Not ignored. Somebody typing into "current" believes something about their
    own account, and silently accepting it teaches them the wrong thing."""
    link_account(admin, browser, "colleague@example.com")

    response = browser.post(
        "/api/auth/me/password",
        json={"current_password": "anything at all", "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 401
    assert browser.get("/api/auth/me").json()["has_password"] is False


def test_an_account_with_a_password_still_has_to_prove_it(admin, db_session):
    """The new optional field must not become a way past the old check.

    This is the one that would matter if it broke: `current_password` is optional
    in the schema, so if the service decided "no current password sent" meant
    "allowed", anybody holding a stolen session could rotate the password and own
    the account outright.
    """
    no_proof = admin.post("/api/auth/me/password", json={"new_password": NEW_PASSWORD})
    assert no_proof.status_code == 401

    wrong = admin.post(
        "/api/auth/me/password",
        json={"current_password": "not it", "new_password": NEW_PASSWORD},
    )
    assert wrong.status_code == 401

    right = admin.post(
        "/api/auth/me/password",
        json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert right.status_code == 200


def test_a_blank_password_never_becomes_a_way_in(admin, browser, app, settings):
    """The D29 hole, re-checked from this side.

    A link account stores `password_hash == ""`. If `verify_password` ever
    stopped refusing an empty stored hash first, every one of them would be
    signable-into by leaving the box blank — and D31 adds a *password form* to
    those accounts, so the reflex to loosen this check is now closer to hand.
    """
    link_account(admin, browser, "colleague@example.com")

    for attempt in ("", " ", "anything"):
        stranger = TestClient(app)
        assert sign_in(stranger, "colleague@example.com", attempt).status_code == 401


# --- an administrator granting one ------------------------------------------


def test_an_administrator_can_set_somebody_password(admin, browser, app, settings):
    """The remedy for the person who has already signed out and lost their link."""
    user_id = link_account(admin, browser, "colleague@example.com")

    response = admin.post(f"/api/auth/users/{user_id}/password", json={"password": NEW_PASSWORD})

    assert response.status_code == 200
    assert sign_in(TestClient(app), "colleague@example.com", NEW_PASSWORD).status_code == 200


def test_setting_somebody_password_ends_every_session_they_have(admin, browser, app, settings):
    """Including the one they are reading on, unlike the self-service change.

    The administrator cannot know which of somebody's sessions is the one that
    needed the reset, so all of them go. Sparing the target's current browser
    would make a reset performed *because* of a suspected compromise decorative.
    """
    user_id = link_account(admin, browser, "colleague@example.com")
    assert browser.get("/api/auth/me").status_code == 200

    response = admin.post(f"/api/auth/users/{user_id}/password", json={"password": NEW_PASSWORD})

    assert response.json()["revoked"] == 1
    assert browser.get("/api/auth/me").status_code == 401


def test_a_member_cannot_set_anybody_password(admin, browser, db_session, settings, app):
    """Including their own colleague's, and including the administrator's.

    A member who could set somebody else's password would not need a role change
    to become them — they would just sign in as them.
    """
    user_id = link_account(admin, browser, "colleague@example.com")
    boss_id = admin.get("/api/auth/users").json()[0]["id"]

    assert (
        browser.post(f"/api/auth/users/{boss_id}/password", json={"password": NEW_PASSWORD}).status_code
        == 403
    )
    assert (
        browser.post(f"/api/auth/users/{user_id}/password", json={"password": NEW_PASSWORD}).status_code
        == 403
    )
    # Nothing moved: the boss still has their own password and nobody else's works.
    assert sign_in(TestClient(app), "boss@example.com", NEW_PASSWORD).status_code == 401
    assert sign_in(TestClient(app), "boss@example.com", TEST_PASSWORD).status_code == 200


def test_a_stranger_cannot_set_anybody_password(browser, admin):
    boss_id = admin.get("/api/auth/users").json()[0]["id"]
    stranger_response = browser.post(f"/api/auth/users/{boss_id}/password", json={"password": NEW_PASSWORD})
    assert stranger_response.status_code in (401, 403)


def test_a_weak_password_is_refused_wherever_it_is_set(admin, browser):
    """Same rule for all three doors, or the weakest one becomes the policy."""
    user_id = link_account(admin, browser, "colleague@example.com")

    assert browser.post("/api/auth/me/password", json={"new_password": "short"}).status_code == 422
    assert admin.post(f"/api/auth/users/{user_id}/password", json={"password": "short"}).status_code == 422
    assert (
        admin.post(
            "/api/auth/users",
            json={"email": "new@example.com", "role": "member", "password": "short"},
        ).status_code
        == 422
    )


# --- an administrator creating the account outright -------------------------


def test_an_administrator_can_create_an_account_with_a_password(admin, app):
    """The request that started this: put these people in, with passwords, now."""
    response = admin.post(
        "/api/auth/users",
        json={
            "email": "Yasha@SDSManager.com",
            "display_name": "Yasha",
            "role": "member",
            "password": NEW_PASSWORD,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "yasha@sdsmanager.com", "lowercased like every other address"
    assert body["display_name"] == "Yasha"
    assert body["role"] == ROLE_MEMBER
    assert body["has_password"] is True
    assert "password" not in {k for k in body if k != "has_password"}

    assert sign_in(TestClient(app), "yasha@sdsmanager.com", NEW_PASSWORD).status_code == 200


def test_creating_an_account_signs_nobody_in(admin, app, db_session):
    """No session is started, and no cookie comes back.

    The administrator is creating somebody *else's* account. A response that set
    a cookie would sign the administrator in as the person they just created.
    """
    before = admin.get("/api/auth/me").json()["email"]

    response = admin.post(
        "/api/auth/users",
        json={"email": "arni@sdsmanager.com", "role": "member", "password": NEW_PASSWORD},
    )

    assert "set-cookie" not in {k.lower() for k in response.headers}
    assert admin.get("/api/auth/me").json()["email"] == before


def test_an_administrator_can_create_another_administrator(admin, app):
    response = admin.post(
        "/api/auth/users",
        json={"email": "tanjir@sdsmanager.com", "role": "admin", "password": NEW_PASSWORD},
    )
    assert response.json()["role"] == ROLE_ADMIN
    peer = TestClient(app)
    assert sign_in(peer, "tanjir@sdsmanager.com", NEW_PASSWORD).status_code == 200
    assert peer.get("/api/auth/users").status_code == 200, "and it is a real administrator"


def test_creating_an_account_that_exists_is_refused_rather_than_overwriting(admin, db_session):
    """Otherwise "add this person" silently becomes "reset their password"."""
    admin.post(
        "/api/auth/users",
        json={"email": "rifa@sdsmanager.com", "role": "member", "password": NEW_PASSWORD},
    )
    again = admin.post(
        "/api/auth/users",
        json={"email": "rifa@sdsmanager.com", "role": "admin", "password": "another-long-one"},
    )

    assert again.status_code == 409
    stored = db_session.query(User).filter(User.email == "rifa@sdsmanager.com").one()
    assert stored.role == ROLE_MEMBER, "not re-roled by a failed create"


def test_a_member_cannot_create_an_account(admin, browser, db_session):
    """It would be a way to mint themselves an administrator to sign in as."""
    link_account(admin, browser, "colleague@example.com")

    response = browser.post(
        "/api/auth/users",
        json={"email": "sneaky@example.com", "role": "admin", "password": NEW_PASSWORD},
    )

    assert response.status_code == 403
    assert db_session.query(User).filter(User.email == "sneaky@example.com").count() == 0


@pytest.mark.parametrize("role", ["owner", "superuser", "", "ADMIN"])
def test_an_unknown_role_is_refused(admin, role):
    response = admin.post(
        "/api/auth/users",
        json={"email": "someone@example.com", "role": role, "password": NEW_PASSWORD},
    )
    assert response.status_code in (400, 422)


def test_an_account_created_this_way_keeps_its_own_access_link_story(admin, browser, app, settings):
    """A password and an access link are not exclusive (D29 and D31 together).

    Somebody can be created with a password *and* be on the roster with a link.
    Both work, and neither disturbs the other — which is the shape that makes
    "the link is the easy way in, the password is the way back" true.
    """
    admin.post(
        "/api/auth/users",
        json={"email": "colleague@example.com", "role": "member", "password": NEW_PASSWORD},
    )
    admin.post("/api/auth/roster", json={"addresses": "colleague@example.com", "role": "member"})
    entry = next(
        e for e in admin.get("/api/auth/roster").json()["entries"] if e["email"] == "colleague@example.com"
    )
    token = entry["access_url"].split("accept=")[1]

    by_link = TestClient(app)
    assert by_link.post("/api/auth/accept", json={"token": token}).status_code == 200
    by_password = TestClient(app)
    assert sign_in(by_password, "colleague@example.com", NEW_PASSWORD).status_code == 200

    # And the link did not wipe the password on its way past.
    assert by_link.get("/api/auth/me").json()["has_password"] is True
