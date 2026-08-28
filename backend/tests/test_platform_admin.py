"""One address that cannot be demoted, deactivated, or moved off itself (D29).

The last-administrator guard protects the *deployment* — it stops anyone
removing the final way back in. It does not protect a *person*: with three
administrators, any of them can deactivate any other. `PLATFORM_ADMIN_EMAIL`
names one account as a fixed point.

Every test here names the address through settings rather than by patching a
constant, because the whole design decision is that this is deployment config
and can be lifted without a deploy.
"""

from __future__ import annotations

import pytest

from app.models import ROLE_ADMIN, ROLE_MEMBER, User
from app.services import accounts, roster
from tests.conftest import TEST_PASSWORD, admin_hash, make_account

PROTECTED = "owner@example.com"


@pytest.fixture
def guarded(settings):
    """Settings naming the protected address."""
    return settings.model_copy(update={"platform_admin_email": PROTECTED})


def add_user(db, email: str, role: str = ROLE_ADMIN, active: bool = True) -> User:
    user = User(
        email=email,
        display_name=email.partition("@")[0],
        password_hash=admin_hash(),
        role=role,
        is_active=active,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def people(db_session):
    """A protected owner and two ordinary administrators to act on them."""
    owner = add_user(db_session, PROTECTED)
    other = add_user(db_session, "other@example.com")
    third = add_user(db_session, "third@example.com")
    return owner, other, third


# --- what the guard refuses -------------------------------------------------


def test_the_platform_admin_cannot_be_demoted(db_session, people, guarded):
    owner, other, _ = people
    with pytest.raises(accounts.NotPermitted, match="cannot be demoted"):
        accounts.set_role(db_session, other, owner, ROLE_MEMBER, guarded)
    db_session.refresh(owner)
    assert owner.role == ROLE_ADMIN


def test_the_platform_admin_cannot_be_deactivated(db_session, people, guarded):
    owner, other, _ = people
    with pytest.raises(accounts.NotPermitted, match="cannot be deactivated"):
        accounts.set_active(db_session, other, owner, False, guarded)
    db_session.refresh(owner)
    assert owner.is_active is True


def test_the_guard_holds_even_with_other_administrators_present(db_session, people, guarded):
    """The distinction from the last-administrator rule, stated as a test.

    Three admins exist, so `admin_count > 1` and the pre-existing guard would
    happily allow this. Only the platform-admin rule stops it.
    """
    owner, other, third = people
    assert accounts.admin_count(db_session) == 3
    with pytest.raises(accounts.NotPermitted):
        accounts.set_active(db_session, other, owner, False, guarded)
    with pytest.raises(accounts.NotPermitted):
        accounts.set_role(db_session, third, owner, ROLE_MEMBER, guarded)


def test_the_platform_admin_cannot_move_off_its_own_address(db_session, people, guarded):
    """Otherwise the protection is one self-service edit away from gone, by the
    one person least likely to notice - nothing about their session changes."""
    owner, _, _ = people
    with pytest.raises(accounts.NotPermitted, match="fixed by the deployment"):
        accounts.update_profile(
            db_session, owner, display_name=None, email="elsewhere@example.com", settings=guarded
        )
    db_session.refresh(owner)
    assert owner.email == PROTECTED


def test_the_platform_admin_may_still_change_their_display_name(db_session, people, guarded):
    """The address is fixed; the person is not frozen."""
    owner, _, _ = people
    accounts.update_profile(db_session, owner, display_name="Renamed", email=None, settings=guarded)
    db_session.refresh(owner)
    assert owner.display_name == "Renamed"
    assert owner.email == PROTECTED


def test_the_platform_admin_cannot_be_taken_off_the_roster(db_session, people, guarded):
    owner, other, _ = people
    added, _ = roster.add_addresses(db_session, other, raw=PROTECTED, role=ROLE_ADMIN, note="")
    entry = added[0]
    with pytest.raises(accounts.NotPermitted, match="cannot be removed from the roster"):
        roster.remove_entry(db_session, other, entry, guarded)


# --- what the guard must NOT do ---------------------------------------------


def test_an_ordinary_administrator_is_still_removable(db_session, people, guarded):
    """A targeted guard, not a blanket freeze on administration."""
    owner, other, third = people
    accounts.set_role(db_session, owner, other, ROLE_MEMBER, guarded)
    db_session.refresh(other)
    assert other.role == ROLE_MEMBER

    accounts.set_active(db_session, owner, third, False, guarded)
    db_session.refresh(third)
    assert third.is_active is False


def test_an_unset_variable_protects_nobody(db_session, people, settings):
    """The default is the behaviour that existed before this feature."""
    owner, other, _ = people
    assert settings.platform_admin_email == ""
    accounts.set_active(db_session, other, owner, False, settings)
    db_session.refresh(owner)
    assert owner.is_active is False


def test_the_platform_admin_can_still_be_promoted_and_reactivated(db_session, people, guarded):
    """Refusals are one-directional: the guard blocks removal, not repair."""
    owner, other, _ = people
    owner.role = ROLE_MEMBER
    owner.is_active = False
    db_session.commit()

    accounts.set_role(db_session, other, owner, ROLE_ADMIN, guarded)
    accounts.set_active(db_session, other, owner, True, guarded)
    db_session.refresh(owner)
    assert owner.role == ROLE_ADMIN and owner.is_active is True


# --- matching ---------------------------------------------------------------


@pytest.mark.parametrize("configured", ["  Owner@Example.COM  ", "OWNER@EXAMPLE.COM", PROTECTED])
def test_the_address_matches_however_the_variable_was_typed(db_session, people, settings, configured):
    owner, other, _ = people
    guarded = settings.model_copy(update={"platform_admin_email": configured})
    with pytest.raises(accounts.NotPermitted):
        accounts.set_active(db_session, other, owner, False, guarded)


def test_a_malformed_variable_protects_nobody_rather_than_everybody(db_session, people, settings):
    """A typo must not turn into a workspace nobody can administer."""
    owner, other, _ = people
    broken = settings.model_copy(update={"platform_admin_email": "not-an-address"})
    accounts.set_active(db_session, other, owner, False, broken)
    db_session.refresh(owner)
    assert owner.is_active is False


# --- over HTTP --------------------------------------------------------------


def test_the_api_refuses_with_403(db_session, monkeypatch, guarded):
    """The refusal has to reach the dashboard as a 403, not a 500."""
    from fastapi.testclient import TestClient

    from tests.conftest import _build_app

    owner = add_user(db_session, PROTECTED)
    app = _build_app(db_session, monkeypatch, guarded)
    _, token = make_account(db_session, guarded, email="admin2@example.com")
    client = TestClient(app, cookies={guarded.session_cookie_name: token})

    demote = client.patch(f"/api/auth/users/{owner.id}", json={"role": "member"})
    assert demote.status_code == 403, demote.text
    assert "platform administrator" in demote.json()["detail"]

    off = client.patch(f"/api/auth/users/{owner.id}", json={"is_active": False})
    assert off.status_code == 403, off.text

    assert TEST_PASSWORD  # imported for the fixture's sake; keeps linters honest
