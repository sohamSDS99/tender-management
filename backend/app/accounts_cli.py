"""Account administration from the server: ``python -m app.accounts_cli``.

This exists because of a hole the browser cannot cover. Registration is
invite-only after the first account (D25), invites are issued by administrators,
and this product has no mail transport - Slack posts to a channel, not to a
person. So there are two states the dashboard alone cannot get you out of:

* every administrator has forgotten their password, or
* somebody on the network reached the dashboard before you did and took the
  bootstrap admin slot.

Both are recoverable from a shell on the host, which is the same trust boundary
that already owns the database file and the .env. Nothing here is reachable over
HTTP.

    python -m app.accounts_cli create-admin --email you@example.com --name "You"
    python -m app.accounts_cli reset-password --email you@example.com
    python -m app.accounts_cli invite --email colleague@example.com
    python -m app.accounts_cli list

Passwords are prompted for rather than passed as arguments by default: an
argument is visible in `ps` and lands in the shell history of whoever ran it.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.db import SessionLocal, init_db
from app.models import ROLE_ADMIN, ROLE_MEMBER, ROLES, User
from app.services import accounts
from app.settings import get_settings


def _read_password(supplied: str | None) -> str:
    if supplied:
        return supplied
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("Those passwords do not match.")
    return first


def _create_user(args: argparse.Namespace, *, role: str) -> int:
    settings = get_settings()
    password = _read_password(args.password)
    db = SessionLocal()
    try:
        email = accounts.normalise_email(args.email)
        if accounts.get_by_email(db, email) is not None:
            raise SystemExit(f"{email} already has an account. Use reset-password instead.")
        accounts.check_password(password, email, settings)
        user = User(
            email=email,
            display_name=accounts.clean_display_name(args.name or "", email),
            password_hash=accounts.hash_password(password),
            role=role,
            is_active=True,
        )
        db.add(user)
        db.commit()
        print(f"Created {role} {user.email} (id {user.id}).")
    finally:
        db.close()
    return 0


def _create_admin(args: argparse.Namespace) -> int:
    return _create_user(args, role=ROLE_ADMIN)


def _create_person(args: argparse.Namespace) -> int:
    """``create-user --role`` — the shell twin of POST /api/auth/users (D31).

    The dashboard can do this now, and doing it there is better because it does
    not need a shell on the host. This stays for the case the dashboard cannot
    help with: nobody able to sign in as an administrator.
    """
    return _create_user(args, role=args.role)


def _reset_password(args: argparse.Namespace) -> int:
    """Set a new password and end every session that account had.

    Ending them is not optional here. This command is what you run when you
    think somebody else may be holding the old password, and leaving their
    cookie alive would make the reset decorative.
    """
    settings = get_settings()
    password = _read_password(args.password)
    db = SessionLocal()
    try:
        user = accounts.get_by_email(db, accounts.normalise_email(args.email))
        if user is None:
            raise SystemExit(f"No account for {args.email}.")
        accounts.check_password(password, user.email, settings)
        user.password_hash = accounts.hash_password(password)
        user.failed_logins = 0
        user.locked_until = None
        if args.reactivate:
            user.is_active = True
        ended = accounts.revoke_sessions(db, user, except_id=None, commit=False)
        db.commit()
        print(f"Password reset for {user.email}. {ended} session(s) ended.")
    finally:
        db.close()
    return 0


def _invite(args: argparse.Namespace) -> int:
    settings = get_settings()
    db = SessionLocal()
    try:
        issuer = db.query(User).filter(User.role == ROLE_ADMIN).order_by(User.id).first()
        if issuer is None:
            raise SystemExit("No administrator exists yet. Run create-admin first.")
        token, invite = accounts.create_invite(
            db, issuer, email=args.email, role=args.role, note=args.note or "", settings=settings
        )
        print(f"Invitation {invite.id} for {invite.email or 'anyone'} as {invite.role}.")
        print(f"Expires {invite.expires_at.isoformat()}Z. Send this link:")
        print(accounts.invite_url(token, settings))
    finally:
        db.close()
    return 0


def _list(_: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        users = accounts.list_users(db)
        if not users:
            print("No accounts yet. The next registration in the dashboard becomes the admin.")
            return 0
        width = max(len(u.email) for u in users)
        for user in users:
            state = "active" if user.is_active else "deactivated"
            seen = user.last_login_at.isoformat() + "Z" if user.last_login_at else "never"
            print(f"{user.email:<{width}}  {user.role:<6}  {state:<11}  last sign-in {seen}")
    finally:
        db.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.accounts_cli", description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)

    create = subs.add_parser("create-admin", help="create an administrator account")
    create.add_argument("--email", required=True)
    create.add_argument("--name", default="")
    create.add_argument("--password", default=None, help="prompted for if omitted (preferred)")
    create.set_defaults(func=_create_admin)

    person = subs.add_parser("create-user", help="create an account with a role and a password")
    person.add_argument("--email", required=True)
    person.add_argument("--name", default="")
    person.add_argument("--role", default=ROLE_MEMBER, choices=list(ROLES))
    person.add_argument("--password", default=None, help="prompted for if omitted (preferred)")
    person.set_defaults(func=_create_person)

    reset = subs.add_parser("reset-password", help="set a new password and end that user's sessions")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password", default=None, help="prompted for if omitted (preferred)")
    reset.add_argument("--reactivate", action="store_true", help="also re-enable a deactivated account")
    reset.set_defaults(func=_reset_password)

    invite = subs.add_parser("invite", help="issue an invitation link")
    invite.add_argument("--email", default=None, help="restrict the invite to this address")
    invite.add_argument("--role", default=ROLE_MEMBER, choices=list(ROLES))
    invite.add_argument("--note", default="")
    invite.set_defaults(func=_invite)

    listing = subs.add_parser("list", help="list accounts")
    listing.set_defaults(func=_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    init_db()
    try:
        return int(args.func(args))
    except accounts.AccountError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
