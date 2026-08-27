"""Accounts: register, sign in, sign out, profile, invites, administration.

Kept in its own module rather than added to ``routes.py`` because it is a
different kind of surface. Everything in ``routes.py`` is about procurement
notices and is readable by anyone; everything here is about people, and roughly
half of it is the only part of the API that refuses a caller (D25).

The whole of the credential logic lives in ``app/services/accounts.py``. This
module does three things and no more: it turns a cookie into a caller, it turns
an ``AccountError`` into a status code, and it decides what the browser is told.

**Every failure here is deliberately vague about which part failed.** The
service raises one error for an unknown address, a wrong password and a
deactivated account; that is not laziness, and re-splitting it to be friendlier
would turn the sign-in form into a directory of who works here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Invite, RosterEntry, User, UserSession, utcnow
from app.schemas import (
    InviteCreate,
    InviteCreated,
    InviteOut,
    JoinLink,
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    RegisterRequest,
    RevokedCount,
    RosterAdd,
    RosterAdded,
    RosterEntryOut,
    RosterRoleUpdate,
    RosterView,
    SessionOut,
    SessionState,
    UserAdminUpdate,
    UserOut,
)
from app.security import Principal, current_principal, require_admin, require_principal, settings_dep
from app.services import accounts, roster
from app.settings import Settings

router = APIRouter(prefix="/api/auth", tags=["accounts"])


def _fail(exc: accounts.AccountError) -> HTTPException:
    """One translation point, so a new error type cannot arrive as a 500."""
    return HTTPException(status_code=exc.status, detail=exc.message)


def _set_cookie(response: Response, token: str, settings: Settings) -> None:
    """Write the session cookie.

    ``samesite="lax"`` is the CSRF control for this API. The dashboard is served
    from the same origin as the API in both supported deployments (Vite proxies
    in development, the container's web server proxies in production), so Lax
    costs nothing here and stops another site's form from posting as you. It is
    the whole defence, which is acceptable because the endpoints that spend
    money were never credential-gated in the first place (D23) - there is no
    privilege to ride.

    ``secure`` is configuration rather than a constant because the documented
    deployment is plain HTTP on a LAN, where a Secure cookie is silently never
    sent: the symptom is a sign-in that returns 200 and leaves you signed out.
    """
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_lifetime_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )


def _user_out(user: User) -> UserOut:
    return UserOut.model_validate(user)


def _session_out(session: UserSession, current_id: int) -> SessionOut:
    return SessionOut(
        id=session.id,
        created_at=session.created_at,
        last_seen_at=session.last_seen_at,
        expires_at=session.expires_at,
        user_agent=session.user_agent,
        current=session.id == current_id,
    )


def _invite_out(invite: Invite) -> InviteOut:
    return InviteOut(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        note=invite.note,
        status=invite.status(utcnow()),
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        accepted_at=invite.accepted_at,
    )


# --- session ----------------------------------------------------------------


@router.get("/session", response_model=SessionState, summary="Who is signed in, if anyone")
def read_session(
    response: Response,
    request: Request,
    principal: Principal | None = Depends(current_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> SessionState:
    """The dashboard's first call, and it must never fail.

    An anonymous reader is a 200 with ``user: null``, not a 401. That is a
    deliberate contract: the dashboard works signed out, so "nobody is signed
    in" is an ordinary answer and not an error to handle. Returning 401 here
    would put a red line in every anonymous reader's console and invite exactly
    the wrong reflex - treating a normal state as a failure to recover from.

    A cookie that no longer resolves is cleared on the way out. Otherwise a
    revoked or expired session sits in the browser being re-sent and re-rejected
    on every page load for the rest of its Max-Age.
    """
    if principal is None and request.cookies.get(settings.session_cookie_name):
        _clear_cookie(response, settings)
    return SessionState(
        user=_user_out(principal.user) if principal else None,
        bootstrap=accounts.user_count(db) == 0,
        invite_required=accounts.user_count(db) > 0,
    )


@router.post("/register", response_model=UserOut, status_code=201, summary="Create an account")
def register(
    payload: RegisterRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> UserOut:
    """Register, and sign the new account in immediately.

    Signing in as part of registering is the point: making someone re-enter the
    password they just chose proves nothing and loses people at the last step.
    """
    try:
        user = accounts.register(
            db,
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            invite_token=payload.invite_token,
            join_token=payload.join_token,
            settings=settings,
        )
    except accounts.AccountError as exc:
        raise _fail(exc) from None

    token, _ = accounts.start_session(
        db, user, user_agent=request.headers.get("user-agent", ""), settings=settings
    )
    _set_cookie(response, token, settings)
    return _user_out(user)


@router.post("/login", response_model=UserOut, summary="Sign in")
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> UserOut:
    try:
        user = accounts.authenticate(db, email=payload.email, password=payload.password, settings=settings)
    except accounts.AccountError as exc:
        raise _fail(exc) from None

    token, _ = accounts.start_session(
        db, user, user_agent=request.headers.get("user-agent", ""), settings=settings
    )
    _set_cookie(response, token, settings)
    return _user_out(user)


@router.post("/logout", status_code=204, summary="Sign out of this browser")
def logout(
    response: Response,
    principal: Principal | None = Depends(current_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> Response:
    """Ends this session only, and always clears the cookie.

    Not behind ``require_principal``: signing out when you are already signed
    out is not an error, and answering 401 to "log me out" is the kind of thing
    that leaves a stale cookie in place because the client trusted the status.
    """
    if principal is not None:
        accounts.end_session(db, principal.session)
    out = Response(status_code=204)
    _clear_cookie(out, settings)
    return out


# --- profile ----------------------------------------------------------------


@router.get("/me", response_model=UserOut, summary="Your own profile")
def read_me(principal: Principal = Depends(require_principal)) -> UserOut:
    return _user_out(principal.user)


@router.patch("/me", response_model=UserOut, summary="Change your name or email")
def update_me(
    payload: ProfileUpdate,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> UserOut:
    try:
        user = accounts.update_profile(
            db, principal.user, display_name=payload.display_name, email=payload.email
        )
    except accounts.AccountError as exc:
        raise _fail(exc) from None
    return _user_out(user)


@router.post("/me/password", response_model=RevokedCount, summary="Change your password")
def change_password(
    payload: PasswordChange,
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> RevokedCount:
    """Rotates the password and ends every *other* session.

    The count comes back so the dashboard can say what happened. "Password
    changed" alone leaves a person wondering whether the laptop they are worried
    about is still signed in; "password changed, 2 other sessions ended" answers
    the question they actually had.
    """
    try:
        revoked = accounts.change_password(
            db,
            principal.user,
            current_password=payload.current_password,
            new_password=payload.new_password,
            keep_session_id=principal.session.id,
            settings=settings,
        )
    except accounts.AccountError as exc:
        raise _fail(exc) from None
    return RevokedCount(revoked=revoked)


@router.get("/sessions", response_model=list[SessionOut], summary="Your signed-in browsers")
def list_sessions(
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> list[SessionOut]:
    rows = accounts.list_sessions(db, principal.user)
    return [_session_out(row, principal.session.id) for row in rows]


@router.delete("/sessions", response_model=RevokedCount, summary="Sign out everywhere else")
def revoke_other_sessions(
    principal: Principal = Depends(require_principal),
    db: Session = Depends(get_db),
) -> RevokedCount:
    """Ends every session but this one.

    Sparing the current browser is what makes this a safe button to press: the
    alternative signs you out as well, so the confirmation of success is the
    sign-in screen, and nobody can tell that from a failure.
    """
    revoked = accounts.revoke_sessions(db, principal.user, except_id=principal.session.id)
    return RevokedCount(revoked=revoked)


# --- administration ---------------------------------------------------------


@router.get("/invites", response_model=list[InviteOut], summary="Invitations, including used ones")
def list_invites(_: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> list[InviteOut]:
    return [_invite_out(invite) for invite in accounts.list_invites(db)]


@router.post("/invites", response_model=InviteCreated, status_code=201, summary="Invite someone")
def create_invite(
    payload: InviteCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> InviteCreated:
    """Issue a single-use link. The token is in this response and nowhere else.

    There is no mail transport in this product (Slack is the only outbound
    channel, and it posts to a channel rather than to a person), so the link is
    handed back to the administrator to deliver however they already talk to the
    person they are inviting.
    """
    try:
        token, invite = accounts.create_invite(
            db,
            principal.user,
            email=payload.email,
            role=payload.role,
            note=payload.note,
            settings=settings,
        )
    except accounts.AccountError as exc:
        raise _fail(exc) from None
    return InviteCreated(invite=_invite_out(invite), token=token, url=accounts.invite_url(token, settings))


@router.delete("/invites/{invite_id}", status_code=204, summary="Withdraw an invitation")
def revoke_invite(
    invite_id: int,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    invite = db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="No such invitation.")
    try:
        accounts.revoke_invite(db, invite)
    except accounts.AccountError as exc:
        raise _fail(exc) from None
    return Response(status_code=204)


@router.get("/users", response_model=list[UserOut], summary="Everyone with an account")
def list_users(_: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> list[UserOut]:
    return [_user_out(user) for user in accounts.list_users(db)]


@router.patch("/users/{user_id}", response_model=UserOut, summary="Change a role, or deactivate")
def update_user(
    user_id: int,
    payload: UserAdminUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    """The two administrative changes, both refusing to strand the deployment.

    An administrator cannot demote or deactivate the last remaining
    administrator, and cannot deactivate themselves. Without the first rule one
    click leaves nobody able to invite anyone; without the second, the click
    that does it is a very easy one to make by accident.
    """
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No such account.")
    try:
        if payload.role is not None:
            target = accounts.set_role(db, principal.user, target, payload.role)
        if payload.is_active is not None:
            target = accounts.set_active(db, principal.user, target, payload.is_active)
    except accounts.AccountError as exc:
        raise _fail(exc) from None
    return _user_out(target)


# --- the workspace roster (D28) ---------------------------------------------


def _roster_out(entry: RosterEntry) -> RosterEntryOut:
    return RosterEntryOut(
        id=entry.id,
        email=entry.email,
        role=entry.role,
        note=entry.note,
        created_at=entry.created_at,
        joined_at=entry.joined_at,
    )


def _roster_view(db: Session, settings: Settings) -> RosterView:
    token = roster.get_join_token(db)
    return RosterView(
        entries=[_roster_out(e) for e in roster.list_entries(db)],
        **roster.counts(db),
        join_url=roster.join_url(token, settings) if token else None,
    )


@router.get("/roster", response_model=RosterView, summary="Who may hold an account")
def read_roster(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> RosterView:
    """The roster and the join link together.

    One call rather than two, because the panel is useless without both: an
    administrator opening it is almost always there to copy the link or to add
    somebody who needs it.
    """
    return _roster_view(db, settings)


@router.post("/roster", response_model=RosterAdded, status_code=201, summary="Add addresses")
def add_to_roster(
    payload: RosterAdd,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> RosterAdded:
    """Add a pasted list of addresses. Adding is not sending.

    Nobody is notified — this records who is *allowed* in. Delivering the join
    link stays a separate, deliberate act, which is why the link is shown
    alongside rather than fired off automatically.
    """
    try:
        added, existing = roster.add_addresses(
            db, principal.user, raw=payload.addresses, role=payload.role, note=payload.note
        )
    except accounts.AccountError as exc:
        raise _fail(exc) from None
    return RosterAdded(added=[_roster_out(e) for e in added], already_present=existing)


@router.patch("/roster/{entry_id}", response_model=RosterEntryOut, summary="Change the role on joining")
def update_roster_entry(
    entry_id: int,
    payload: RosterRoleUpdate,
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> RosterEntryOut:
    """Changes what a *future* account gets, not an existing one.

    Somebody who joined last week keeps the role they were given. Moving them is
    what PATCH /api/auth/users/{id} is for, and doing it as a side effect of a
    roster edit would be a change nobody asked for, in the wrong place.
    """
    entry = db.get(RosterEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such roster entry.")
    try:
        entry = roster.set_entry_role(db, entry, payload.role)
    except accounts.AccountError as exc:
        raise _fail(exc) from None
    return _roster_out(entry)


@router.delete("/roster/{entry_id}", status_code=204, summary="Remove an address")
def remove_from_roster(
    entry_id: int,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Withdraws permission to register. Does **not** close an existing account.

    Two different acts with different consequences: this one stops somebody
    signing up, while ending a current person's access kills their sessions and
    is guarded against stranding the last administrator. Conflating them would
    let a roster tidy-up lock everybody out.
    """
    entry = db.get(RosterEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such roster entry.")
    try:
        roster.remove_entry(db, principal.user, entry)
    except accounts.AccountError as exc:
        raise _fail(exc) from None
    return Response(status_code=204)


@router.post("/roster/join-link", response_model=JoinLink, summary="Create or replace the join link")
def rotate_join_link(
    _: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> JoinLink:
    """Mints a new link and kills the previous one.

    The same endpoint creates the first link and replaces a leaked one, because
    they are the same operation and a separate "create" would be a second door
    to keep in step. Anyone who has already registered is unaffected: the token
    grants registration and nothing else.
    """
    token = roster.rotate_join_token(db)
    return JoinLink(url=roster.join_url(token, settings), token=token)
