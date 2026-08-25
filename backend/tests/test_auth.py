"""Accounts: registration, sign-in, sessions, invites and administration (D25).

Two things these tests are guarding that are easy to lose in a refactor:

* **Accounts gate nothing.** ``test_reads_stay_open_after_accounts_exist`` is
  the regression for the whole of D25. If somebody later adds
  ``Depends(require_principal)`` to a tender route, that test is what says so.
* **Sign-in failures are indistinguishable.** An unknown address, a wrong
  password and a deactivated account must answer identically, or the form is a
  staff directory.

The client fixtures carry cookies between requests, which is the whole point -
these exercise the real cookie round-trip rather than calling the service.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.models import User, UserSession, utcnow
from app.services import accounts

GOOD_PASSWORD = "correct-horse-battery"
OTHER_PASSWORD = "a-different-long-password"


def register_first_admin(
    client: TestClient, email: str = "first@example.com", password: str = GOOD_PASSWORD
) -> dict:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": "First Admin"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def issue_invite(client: TestClient, **body: object) -> dict:
    response = client.post("/api/auth/invites", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --- the shape of an empty deployment ---------------------------------------


def test_session_is_a_200_for_a_stranger(anon_client: TestClient):
    """Signed out is an ordinary state, not an error.

    The dashboard calls this on every page load and works signed out, so a 401
    here would put a red line in the console of every anonymous reader.
    """
    response = anon_client.get("/api/auth/session")
    assert response.status_code == 200
    body = response.json()
    assert body["user"] is None
    assert body["bootstrap"] is True
    assert body["invite_required"] is False


def test_first_registration_takes_the_admin_slot(anon_client: TestClient):
    user = register_first_admin(anon_client)
    assert user["role"] == "admin"
    assert user["is_active"] is True
    # Registering signs you in; making someone retype the password they just
    # chose proves nothing.
    session = anon_client.get("/api/auth/session").json()
    assert session["user"]["email"] == "first@example.com"
    assert session["bootstrap"] is False
    assert session["invite_required"] is True


def test_the_second_registration_needs_an_invite(anon_client: TestClient):
    register_first_admin(anon_client)
    response = anon_client.post(
        "/api/auth/register",
        json={"email": "second@example.com", "password": GOOD_PASSWORD, "display_name": "Two"},
    )
    assert response.status_code == 400
    assert "invite" in response.json()["detail"].lower()


def test_registration_never_leaks_the_password_hash(anon_client: TestClient):
    body = register_first_admin(anon_client)
    assert "password" not in str(body).lower()
    assert set(body) == {
        "id",
        "email",
        "display_name",
        "role",
        "is_active",
        "created_at",
        "last_login_at",
    }


# --- the invite chain -------------------------------------------------------


def test_an_invite_admits_exactly_one_person(anon_client, db_session, monkeypatch, settings):
    from tests.conftest import _build_app

    register_first_admin(anon_client)
    invite = issue_invite(anon_client, email="colleague@example.com", note="Bids desk")
    assert invite["invite"]["status"] == "pending"
    assert invite["url"].endswith(invite["token"])

    joiner = TestClient(_build_app(db_session, monkeypatch, settings))
    created = joiner.post(
        "/api/auth/register",
        json={
            "email": "colleague@example.com",
            "password": OTHER_PASSWORD,
            "display_name": "Colleague",
            "invite_token": invite["token"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "member"

    # Single use. A link that admits two people is not an invitation, it is open
    # registration with an extra step.
    again = TestClient(_build_app(db_session, monkeypatch, settings))
    reused = again.post(
        "/api/auth/register",
        json={
            "email": "third@example.com",
            "password": OTHER_PASSWORD,
            "display_name": "Third",
            "invite_token": invite["token"],
        },
    )
    assert reused.status_code == 400
    assert "already been used" in reused.json()["detail"]


def test_an_invite_addressed_to_someone_refuses_anyone_else(anon_client: TestClient):
    register_first_admin(anon_client)
    invite = issue_invite(anon_client, email="named@example.com")
    anon_client.post("/api/auth/logout")
    response = anon_client.post(
        "/api/auth/register",
        json={
            "email": "forwarded@example.com",
            "password": OTHER_PASSWORD,
            "display_name": "Forwarded",
            "invite_token": invite["token"],
        },
    )
    assert response.status_code == 400
    assert "named@example.com" in response.json()["detail"]


def test_an_open_invite_admits_whoever_holds_it(anon_client: TestClient):
    """No address on the invite means "send this to whoever takes the role"."""
    register_first_admin(anon_client)
    invite = issue_invite(anon_client, role="member")
    anon_client.post("/api/auth/logout")
    response = anon_client.post(
        "/api/auth/register",
        json={
            "email": "whoever@example.com",
            "password": OTHER_PASSWORD,
            "display_name": "Whoever",
            "invite_token": invite["token"],
        },
    )
    assert response.status_code == 201, response.text


def test_an_expired_invite_is_refused_and_says_so(anon_client, db_session):
    from app.models import Invite

    register_first_admin(anon_client)
    invite = issue_invite(anon_client)
    row = db_session.get(Invite, invite["invite"]["id"])
    row.expires_at = utcnow() - timedelta(minutes=1)
    db_session.commit()

    anon_client.post("/api/auth/logout")
    response = anon_client.post(
        "/api/auth/register",
        json={
            "email": "late@example.com",
            "password": OTHER_PASSWORD,
            "display_name": "Late",
            "invite_token": invite["token"],
        },
    )
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


def test_a_withdrawn_invite_stops_working(anon_client: TestClient):
    register_first_admin(anon_client)
    invite = issue_invite(anon_client)
    assert anon_client.delete(f"/api/auth/invites/{invite['invite']['id']}").status_code == 204
    listed = anon_client.get("/api/auth/invites").json()
    assert listed[0]["status"] == "revoked"

    anon_client.post("/api/auth/logout")
    response = anon_client.post(
        "/api/auth/register",
        json={
            "email": "revoked@example.com",
            "password": OTHER_PASSWORD,
            "display_name": "Revoked",
            "invite_token": invite["token"],
        },
    )
    assert response.status_code == 400
    assert "withdrawn" in response.json()["detail"]


def test_a_failed_registration_does_not_burn_the_invite(anon_client, db_session, monkeypatch, settings):
    """The invite is only marked used once the account row exists.

    Otherwise a password the server rejects costs the invitee their invitation,
    and they have to go back to an administrator for another one.
    """
    from tests.conftest import _build_app

    register_first_admin(anon_client)
    invite = issue_invite(anon_client)

    joiner = TestClient(_build_app(db_session, monkeypatch, settings))
    rejected = joiner.post(
        "/api/auth/register",
        json={
            "email": "joiner@example.com",
            "password": "short",
            "display_name": "Joiner",
            "invite_token": invite["token"],
        },
    )
    assert rejected.status_code == 422

    accepted = joiner.post(
        "/api/auth/register",
        json={
            "email": "joiner@example.com",
            "password": OTHER_PASSWORD,
            "display_name": "Joiner",
            "invite_token": invite["token"],
        },
    )
    assert accepted.status_code == 201, accepted.text


# --- signing in and out -----------------------------------------------------


def test_sign_in_and_out_round_trip(anon_client: TestClient, settings):
    register_first_admin(anon_client)
    assert anon_client.post("/api/auth/logout").status_code == 204
    assert anon_client.get("/api/auth/me").status_code == 401

    signed_in = anon_client.post(
        "/api/auth/login", json={"email": "first@example.com", "password": GOOD_PASSWORD}
    )
    assert signed_in.status_code == 200
    assert anon_client.get("/api/auth/me").json()["email"] == "first@example.com"

    assert anon_client.post("/api/auth/logout").status_code == 204
    assert anon_client.cookies.get(settings.session_cookie_name) is None
    assert anon_client.get("/api/auth/me").status_code == 401


def test_the_address_is_matched_case_insensitively(anon_client: TestClient):
    """UNIQUE is case-sensitive on both engines, so the write side lowercases.

    Without it "Ada@x.com" registering over "ada@x.com" is two accounts that
    look like one, and neither person can work out why their password fails.
    """
    register_first_admin(anon_client, email="Ada@Example.com")
    anon_client.post("/api/auth/logout")
    response = anon_client.post(
        "/api/auth/login", json={"email": "ADA@example.COM", "password": GOOD_PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"


def test_signing_out_is_not_an_error_when_already_signed_out(anon_client: TestClient):
    """Answering 401 to "log me out" leaves the stale cookie in place."""
    assert anon_client.post("/api/auth/logout").status_code == 204


def test_a_wrong_password_looks_exactly_like_an_unknown_account(anon_client: TestClient):
    register_first_admin(anon_client)
    anon_client.post("/api/auth/logout")

    wrong = anon_client.post(
        "/api/auth/login", json={"email": "first@example.com", "password": "not-the-password"}
    )
    unknown = anon_client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "not-the-password"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_repeated_failures_lock_the_account_then_free_it(anon_client, db_session, settings):
    register_first_admin(anon_client)
    anon_client.post("/api/auth/logout")

    for _ in range(settings.login_max_failures):
        anon_client.post("/api/auth/login", json={"email": "first@example.com", "password": "wrong"})

    locked = anon_client.post(
        "/api/auth/login", json={"email": "first@example.com", "password": GOOD_PASSWORD}
    )
    assert locked.status_code == 429
    assert "try again" in locked.json()["detail"].lower()

    # The lock is a window, not a state a human has to clear.
    user = accounts.get_by_email(db_session, "first@example.com")
    user.locked_until = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert (
        anon_client.post(
            "/api/auth/login", json={"email": "first@example.com", "password": GOOD_PASSWORD}
        ).status_code
        == 200
    )


def test_the_session_cookie_is_httponly_and_lax(anon_client: TestClient, settings):
    """Script cannot read it, and another site's form cannot send it."""
    response = anon_client.post(
        "/api/auth/register",
        json={"email": "first@example.com", "password": GOOD_PASSWORD, "display_name": "First"},
    )
    header = response.headers["set-cookie"].lower()
    assert settings.session_cookie_name in header
    assert "httponly" in header
    assert "samesite=lax" in header
    # False in the fixture settings: a Secure cookie over plain HTTP is never
    # sent, and the documented LAN deployment is plain HTTP.
    assert "secure" not in header


def test_an_unusable_cookie_is_cleared_rather_than_re_sent_for_ever(anon_client, settings):
    anon_client.cookies.set(settings.session_cookie_name, "not-a-real-token")
    response = anon_client.get("/api/auth/session")
    assert response.status_code == 200
    assert response.json()["user"] is None
    assert settings.session_cookie_name in response.headers.get("set-cookie", "")


def test_a_revoked_session_stops_working_immediately(anon_client, db_session):
    register_first_admin(anon_client)
    session = db_session.query(UserSession).one()
    session.revoked_at = utcnow()
    db_session.commit()
    assert anon_client.get("/api/auth/me").status_code == 401


def test_an_expired_session_stops_working(anon_client, db_session):
    register_first_admin(anon_client)
    session = db_session.query(UserSession).one()
    session.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert anon_client.get("/api/auth/me").status_code == 401


# --- the profile ------------------------------------------------------------


def test_a_person_can_change_their_name_and_address(anon_client: TestClient):
    register_first_admin(anon_client)
    response = anon_client.patch(
        "/api/auth/me", json={"display_name": "  Renamed  Person ", "email": "New@Example.com"}
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Renamed Person"
    assert response.json()["email"] == "new@example.com"


def test_a_patch_naming_one_field_does_not_blank_the_other(anon_client: TestClient):
    register_first_admin(anon_client)
    response = anon_client.patch("/api/auth/me", json={"display_name": "Only The Name"})
    assert response.json()["email"] == "first@example.com"


def test_a_blank_name_falls_back_to_the_address_rather_than_emptying(anon_client: TestClient):
    register_first_admin(anon_client)
    response = anon_client.patch("/api/auth/me", json={"display_name": "   "})
    assert response.json()["display_name"] == "first"


def test_an_address_already_in_use_is_refused(anon_client, db_session, monkeypatch, settings):
    from tests.conftest import _build_app

    register_first_admin(anon_client)
    invite = issue_invite(anon_client)
    joiner = TestClient(_build_app(db_session, monkeypatch, settings))
    joiner.post(
        "/api/auth/register",
        json={
            "email": "second@example.com",
            "password": OTHER_PASSWORD,
            "display_name": "Second",
            "invite_token": invite["token"],
        },
    )
    clash = joiner.patch("/api/auth/me", json={"email": "first@example.com"})
    assert clash.status_code == 409


def test_changing_a_password_ends_every_other_session(anon_client, db_session, monkeypatch, settings):
    """The reason to change a password is that someone else may know the old one."""
    from tests.conftest import _build_app

    register_first_admin(anon_client)
    other_browser = TestClient(_build_app(db_session, monkeypatch, settings))
    other_browser.post("/api/auth/login", json={"email": "first@example.com", "password": GOOD_PASSWORD})
    assert other_browser.get("/api/auth/me").status_code == 200

    changed = anon_client.post(
        "/api/auth/me/password",
        json={"current_password": GOOD_PASSWORD, "new_password": OTHER_PASSWORD},
    )
    assert changed.status_code == 200
    assert changed.json()["revoked"] == 1

    assert other_browser.get("/api/auth/me").status_code == 401
    # ...but not this one. Being signed out by your own password change reads
    # as a failure.
    assert anon_client.get("/api/auth/me").status_code == 200


def test_changing_a_password_requires_the_current_one(anon_client: TestClient):
    register_first_admin(anon_client)
    response = anon_client.post(
        "/api/auth/me/password",
        json={"current_password": "not-it", "new_password": OTHER_PASSWORD},
    )
    assert response.status_code == 401
    assert (
        anon_client.post(
            "/api/auth/login", json={"email": "first@example.com", "password": GOOD_PASSWORD}
        ).status_code
        == 200
    )


def test_a_new_password_still_has_to_meet_the_policy(anon_client: TestClient):
    register_first_admin(anon_client)
    response = anon_client.post(
        "/api/auth/me/password", json={"current_password": GOOD_PASSWORD, "new_password": "short"}
    )
    assert response.status_code == 422


def test_the_session_list_marks_which_browser_is_asking(anon_client, db_session, monkeypatch, settings):
    from tests.conftest import _build_app

    register_first_admin(anon_client)
    other = TestClient(_build_app(db_session, monkeypatch, settings))
    other.post("/api/auth/login", json={"email": "first@example.com", "password": GOOD_PASSWORD})

    rows = anon_client.get("/api/auth/sessions").json()
    assert len(rows) == 2
    assert [row["current"] for row in rows].count(True) == 1


def test_signing_out_everywhere_spares_this_browser(anon_client, db_session, monkeypatch, settings):
    from tests.conftest import _build_app

    register_first_admin(anon_client)
    other = TestClient(_build_app(db_session, monkeypatch, settings))
    other.post("/api/auth/login", json={"email": "first@example.com", "password": GOOD_PASSWORD})

    response = anon_client.delete("/api/auth/sessions")
    assert response.json()["revoked"] == 1
    assert other.get("/api/auth/me").status_code == 401
    assert anon_client.get("/api/auth/me").status_code == 200


# --- administration ---------------------------------------------------------


@pytest.fixture
def admin_and_member(anon_client, db_session, monkeypatch, settings):
    """An admin client, a member client, and the member's id."""
    from tests.conftest import _build_app

    register_first_admin(anon_client)
    invite = issue_invite(anon_client)
    member = TestClient(_build_app(db_session, monkeypatch, settings))
    created = member.post(
        "/api/auth/register",
        json={
            "email": "member@example.com",
            "password": OTHER_PASSWORD,
            "display_name": "Member",
            "invite_token": invite["token"],
        },
    )
    return anon_client, member, created.json()["id"]


def test_a_member_cannot_administer_anything(admin_and_member):
    _, member, _ = admin_and_member
    assert member.get("/api/auth/invites").status_code == 403
    assert member.post("/api/auth/invites", json={}).status_code == 403
    assert member.get("/api/auth/users").status_code == 403


def test_a_stranger_gets_401_where_a_member_gets_403(anon_client, admin_and_member):
    """The two are different answers to different questions.

    401 means "identify yourself", 403 means "you have, and it is not enough".
    Collapsing them shows a sign-in form to somebody already signed in.
    """
    admin, member, _ = admin_and_member
    admin.post("/api/auth/logout")
    assert admin.get("/api/auth/users").status_code == 401
    assert member.get("/api/auth/users").status_code == 403


def test_an_admin_can_promote_and_demote(admin_and_member):
    admin, _, member_id = admin_and_member
    promoted = admin.patch(f"/api/auth/users/{member_id}", json={"role": "admin"})
    assert promoted.json()["role"] == "admin"
    demoted = admin.patch(f"/api/auth/users/{member_id}", json={"role": "member"})
    assert demoted.json()["role"] == "member"


def test_the_last_administrator_cannot_be_demoted_or_deactivated(admin_and_member):
    """Otherwise one click leaves nobody able to invite anybody."""
    admin, _, _ = admin_and_member
    admin_id = admin.get("/api/auth/me").json()["id"]

    demote = admin.patch(f"/api/auth/users/{admin_id}", json={"role": "member"})
    assert demote.status_code == 403
    assert "only administrator" in demote.json()["detail"]

    deactivate = admin.patch(f"/api/auth/users/{admin_id}", json={"is_active": False})
    assert deactivate.status_code == 403


def test_an_admin_cannot_deactivate_themselves_even_with_a_spare(admin_and_member):
    admin, _, member_id = admin_and_member
    admin.patch(f"/api/auth/users/{member_id}", json={"role": "admin"})
    admin_id = admin.get("/api/auth/me").json()["id"]
    response = admin.patch(f"/api/auth/users/{admin_id}", json={"is_active": False})
    assert response.status_code == 403
    assert "your own account" in response.json()["detail"]


def test_deactivating_ends_their_sessions_and_refuses_a_new_sign_in(admin_and_member):
    """A deactivated account holding a live cookie is still signed in."""
    admin, member, member_id = admin_and_member
    assert member.get("/api/auth/me").status_code == 200

    assert admin.patch(f"/api/auth/users/{member_id}", json={"is_active": False}).status_code == 200
    assert member.get("/api/auth/me").status_code == 401

    refused = member.post("/api/auth/login", json={"email": "member@example.com", "password": OTHER_PASSWORD})
    assert refused.status_code == 401
    # Same message a wrong password gets: whether an account is switched off is
    # not something the sign-in form should confirm.
    assert refused.json()["detail"] == "Those details do not match an account."


def test_a_reactivated_account_can_sign_in_again(admin_and_member):
    admin, member, member_id = admin_and_member
    admin.patch(f"/api/auth/users/{member_id}", json={"is_active": False})
    admin.patch(f"/api/auth/users/{member_id}", json={"is_active": True})
    assert (
        member.post(
            "/api/auth/login", json={"email": "member@example.com", "password": OTHER_PASSWORD}
        ).status_code
        == 200
    )


def test_an_invite_for_an_address_that_already_has_an_account_is_refused(admin_and_member):
    admin, _, _ = admin_and_member
    response = admin.post("/api/auth/invites", json={"email": "member@example.com"})
    assert response.status_code == 409


# --- D25's actual promise ---------------------------------------------------


def test_reads_stay_open_after_accounts_exist(anon_client, db_session, monkeypatch, settings):
    """The regression for the whole decision.

    Accounts were added on the explicit promise that they gate nothing: a
    signed-out browser sees exactly what it saw before D25. If a
    ``Depends(require_principal)`` ever lands on a tender route, this is the
    test that says so.
    """
    from tests.conftest import _build_app

    register_first_admin(anon_client)
    stranger = TestClient(_build_app(db_session, monkeypatch, settings))
    for path in ("/health", "/api/tenders", "/api/stats", "/api/sources", "/api/automation"):
        assert stranger.get(path).status_code == 200, path


def test_the_operator_actions_are_still_callable_signed_out(anon_client, db_session, monkeypatch, settings):
    """D23's cost limits stayed the control; they did not become a login."""
    from tests.conftest import _build_app

    register_first_admin(anon_client)
    stranger = TestClient(_build_app(db_session, monkeypatch, settings))
    assert stranger.post("/api/tenders/rescore").status_code == 200


# --- the service, directly --------------------------------------------------


def test_a_password_hash_verifies_and_is_salted():
    first = accounts.hash_password(GOOD_PASSWORD)
    second = accounts.hash_password(GOOD_PASSWORD)
    assert first != second, "two hashes of one password must differ, or there is no salt"
    assert accounts.verify_password(GOOD_PASSWORD, first)
    assert not accounts.verify_password("wrong", first)
    assert GOOD_PASSWORD not in first


def test_verifying_against_a_corrupt_hash_is_a_failure_not_a_crash():
    """One unreadable row must not become a 500 the sign-in form cannot explain."""
    for broken in ("", "not-a-hash", "scrypt$x$y$z$q$r", "bcrypt$1$2$3$4$5"):
        assert accounts.verify_password(GOOD_PASSWORD, broken) is False


def test_the_stored_hash_carries_its_own_cost_parameters():
    """So raising the cost later cannot invalidate hashes written before it."""
    stored = accounts.hash_password(GOOD_PASSWORD)
    scheme, n, r, p, _salt, _key = stored.split("$")
    assert scheme == "scrypt"
    assert (int(n), int(r), int(p)) == (accounts.SCRYPT_N, accounts.SCRYPT_R, accounts.SCRYPT_P)


@pytest.mark.parametrize("raw", ["", "  ", "nope", "a@b", "no domain@x", "@example.com"])
def test_an_unusable_address_is_rejected(raw):
    with pytest.raises(accounts.InvalidEmail):
        accounts.normalise_email(raw)


def test_a_password_may_not_be_the_address(settings):
    with pytest.raises(accounts.WeakPassword):
        accounts.check_password("person@example.com", "person@example.com", settings)


def test_a_short_password_is_rejected_with_the_number(settings):
    with pytest.raises(accounts.WeakPassword) as caught:
        accounts.check_password("short", "person@example.com", settings)
    assert str(settings.password_min_length) in str(caught.value)


def test_session_tokens_are_stored_hashed_and_never_in_the_clear(anon_client, db_session):
    """A database dump must not let anybody sign in as anybody."""
    register_first_admin(anon_client)
    raw = anon_client.cookies.get("tm_session")
    session = db_session.query(UserSession).one()
    assert raw
    assert session.token_hash != raw
    assert len(session.token_hash) == 64


def test_a_deleted_users_cookie_stops_working(db_session, settings):
    """And it is ``resolve_session`` that guarantees it, not the foreign key.

    ``user_sessions.user_id`` is declared ON DELETE CASCADE, which PostgreSQL
    enforces and SQLite does not — SQLite ignores foreign keys entirely unless
    ``PRAGMA foreign_keys=ON`` is set per connection, and this app never sets
    it. So on the engine the tests run against, deleting a user leaves the
    session row behind, and an assertion that the row is gone would pass in
    production while proving nothing here.

    What actually closes the hole is the live-user lookup inside
    ``resolve_session``: a session whose user has gone is not a caller on either
    engine. That is the invariant worth pinning, so it is the one asserted.
    """
    user = User(
        email="gone@example.com",
        display_name="Gone",
        password_hash=accounts.hash_password(GOOD_PASSWORD),
        role="member",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    raw, _ = accounts.start_session(db_session, user, user_agent="test", settings=settings)
    assert accounts.resolve_session(db_session, raw, settings) is not None

    db_session.delete(user)
    db_session.commit()
    assert accounts.resolve_session(db_session, raw, settings) is None


def test_registering_counts_as_a_first_sign_in(anon_client: TestClient):
    """Because it mints a session.

    Left null, the profile told somebody "Never signed in" on a page they could
    only be reading because they were signed in.
    """
    assert register_first_admin(anon_client)["last_login_at"] is not None


# --- every value must fit the column it is stored in -------------------------
#
# SQLite ignores VARCHAR limits; PostgreSQL enforces them. That difference has
# already cost this project one silent data loss (see tests/test_schema_fit.py
# and D9), so the caps applied in app/services/accounts.py are asserted against
# the declared widths here rather than only in a live PostgreSQL run.


def test_every_capped_field_fits_its_column(settings):
    from sqlalchemy import String

    from app.models import Invite, User, UserSession

    def width(model, column: str) -> int:
        col = model.__table__.columns[column]
        assert isinstance(col.type, String) and col.type.length
        return col.type.length

    long = "x" * 5_000

    # display_name and note are truncated; email is rejected rather than cut,
    # because silently storing a different address than the one somebody typed
    # would let them sign in never.
    assert len(accounts.clean_display_name(long, "a@b.com")) <= width(User, "display_name")
    with pytest.raises(accounts.InvalidEmail):
        accounts.normalise_email("a" * 320 + "@example.com")
    assert len(accounts.normalise_email("a" * 300 + "@" + "b" * 15 + ".com")) == width(User, "email")
    assert len(" ".join(long.split())[:200]) <= width(Invite, "note")
    # A browser's user-agent is entirely caller-controlled and easily exceeds
    # 400 characters, so start_session truncates it.
    assert width(UserSession, "user_agent") == 400
    assert width(UserSession, "token_hash") == 64, "SHA-256 hex is exactly 64 characters"


def test_a_hostile_user_agent_is_truncated_not_stored_whole(db_session, settings):
    user = User(
        email="ua@example.com",
        display_name="UA",
        password_hash=accounts.hash_password(GOOD_PASSWORD),
        role="member",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    _, session = accounts.start_session(db_session, user, user_agent="z" * 5_000, settings=settings)
    assert len(session.user_agent) == 400
