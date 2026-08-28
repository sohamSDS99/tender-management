import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ApiError, auth as authApi } from '../../api/client';
import type { Auth } from '../../state/auth';
import { formatDateTime } from '../../labels';
import type { Invite, InviteCreated, User, UserRole } from '../../types';
import { Icon } from '../Icon';
import { SettingsRow, SettingsSection } from './SettingsPage';
import { WorkspaceRoster } from './WorkspaceRoster';

/**
 * The administrator's half of the account page: who may join, and who is here.
 *
 * Only rendered for administrators, and every action behind it is refused by
 * the API for anyone else — the hidden UI is a courtesy, not the control.
 *
 * The awkward part of an invite-only system with no mail transport is that the
 * link has to be *delivered by the administrator*, so the one thing this must
 * do well is hand over a link that is easy to copy and impossible to lose by
 * accident. The token exists in exactly one response and is never retrievable
 * again, so a freshly created invitation stays on screen with its link until it
 * is explicitly dismissed.
 */
export function TeamAdmin({ auth }: { auth: Auth }) {
  const [invites, setInvites] = useState<Invite[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextInvites, nextUsers] = await Promise.all([authApi.invites(), authApi.users()]);
      setInvites(nextInvites);
      setUsers(nextUsers);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not read the account list.');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      {/*
        Order follows how the work actually happens. Adding somebody with a
        password comes first because it is now the answer to "put these people
        in" and the only one that leaves them able to sign back in after a sign
        out (D31); the workspace list and its links second, because a link is
        still the frictionless first entry; single-use invitations third, kept for
        the outsider who is not on the list at all; then everyone who already has
        an account.
      */}
      <AddPersonSection onChanged={load} />
      <WorkspaceRoster />
      <InviteSection invites={invites} onChanged={load} />
      <PeopleSection users={users} auth={auth} onChanged={load} error={error} />
    </>
  );
}

/**
 * Add somebody with a password, so signing out is never a lockout (D31).
 *
 * The fourth way an account can exist, and the only one an administrator drives
 * end to end. The other three each hand the password decision to somebody else:
 * bootstrap and a single-use invitation ask the new person to choose one, and an
 * access link never involves a password at all — which is exactly how a colleague
 * ends up able to get in once and then, after pressing Sign out, not again.
 *
 * The password is typed here and delivered by the administrator, like every other
 * credential in this product. It is shown in the clear while being typed on
 * purpose: this is somebody dictating a password to a colleague, and a masked
 * field they cannot read back is how a typo becomes a support request.
 */
function AddPersonSection({ onChanged }: { onChanged: () => void }) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState<UserRole | null>(null);
  const [password, setPassword] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!role) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const created = await authApi.createUser({
        email: email.trim(),
        display_name: name.trim(),
        role,
        password,
      });
      setEmail('');
      setName('');
      setPassword('');
      setStatus(
        `${created.email} can now sign in with that password. Send it to them — nothing was emailed.`,
      );
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not create that account.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsSection
      title="Add a person"
      note="Creates the account and sets its password, so they can sign in with their email address from the start — and again after they sign out. Nobody is emailed; send them the password yourself."
    >
      <form onSubmit={submit}>
        <SettingsRow label="Email">
          <input
            className="input"
            type="email"
            required
            value={email}
            placeholder="colleague@sdsmanager.com"
            autoComplete="off"
            onChange={(event) => setEmail(event.target.value)}
          />
        </SettingsRow>
        <SettingsRow label="Name" hint="Optional. Their address is used if you leave it blank.">
          <input
            className="input"
            type="text"
            value={name}
            placeholder="How their name should appear"
            autoComplete="off"
            onChange={(event) => setName(event.target.value)}
          />
        </SettingsRow>
        <SettingsRow
          label="Role"
          hint="Required. An administrator can add people and change roles; a member reads tenders and keeps a profile."
        >
          <div className="seg" role="group" aria-label="Role for the new account">
            {(['member', 'admin'] as UserRole[]).map((value) => (
              <button
                key={value}
                type="button"
                className={role === value ? 'is-on' : undefined}
                aria-pressed={role === value}
                onClick={() => setRole(value)}
              >
                {value === 'admin' ? 'Administrator' : 'Member'}
              </button>
            ))}
          </div>
        </SettingsRow>
        <SettingsRow
          label="Password"
          hint="Shown as you type, because you have to read it back to them. They can change it themselves once they are in."
        >
          <input
            className="input"
            type="text"
            required
            value={password}
            autoComplete="new-password"
            onChange={(event) => setPassword(event.target.value)}
          />
        </SettingsRow>
        <div className="acct__actions">
          <button
            type="submit"
            className="btn btn--primary btn--sm"
            disabled={busy || !email.trim() || !password || role === null}
            title={role === null ? 'Choose a role first' : undefined}
          >
            {busy ? 'Creating…' : 'Create the account'}
          </button>
          {role === null && email.trim() ? (
            <span className="muted">Choose Member or Administrator.</span>
          ) : null}
          {status ? <span className="acct__ok">{status}</span> : null}
          {error ? (
            <span className="acct__err" role="alert">
              {error}
            </span>
          ) : null}
        </div>
      </form>
    </SettingsSection>
  );
}

function InviteSection({ invites, onChanged }: { invites: Invite[]; onChanged: () => void }) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<UserRole>('member');
  const [note, setNote] = useState('');
  const [created, setCreated] = useState<InviteCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const issue = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await authApi.createInvite({
        email: email.trim() || null,
        role,
        note: note.trim(),
      });
      setCreated(result);
      setCopied(false);
      setEmail('');
      setNote('');
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not create the invitation.');
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.url);
      setCopied(true);
    } catch {
      // Clipboard access needs a secure context, and the documented deployment
      // is plain HTTP on a LAN — so this fails routinely and must not look like
      // a fault. The link is on screen and selectable either way.
      setCopied(false);
    }
  };

  const revoke = async (id: number) => {
    try {
      await authApi.revokeInvite(id);
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not withdraw it.');
    }
  };

  const pending = invites.filter((invite) => invite.status === 'pending');
  const past = invites.filter((invite) => invite.status !== 'pending');

  return (
    <SettingsSection
      title="Invitations"
      note="For somebody who is not on the workspace list — a contractor, a one-off. Single-use, expiring, and shown here exactly once."
    >
      {created ? (
        <div className="invite__new">
          <p className="invite__newhead">
            <Icon name="check" size={14} />
            Invitation created for <b>{created.invite.email ?? 'anyone with the link'}</b>. This
            link is shown once.
          </p>
          <div className="invite__link">
            <input
              className="input mono"
              readOnly
              value={created.url}
              onFocus={(e) => e.target.select()}
            />
            <button type="button" className="btn btn--sm" onClick={() => void copy()}>
              <Icon name="copy" size={13} />
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button
              type="button"
              className="btn btn--sm btn--ghost"
              onClick={() => setCreated(null)}
            >
              Done
            </button>
          </div>
          <p className="muted">Expires {formatDateTime(created.invite.expires_at)}.</p>
        </div>
      ) : null}

      <form onSubmit={issue}>
        <SettingsRow
          label="Address"
          hint="Optional. Set it and only that address can use the link, which makes a forwarded one useless."
        >
          <input
            className="input"
            type="email"
            value={email}
            placeholder="colleague@example.com"
            onChange={(event) => setEmail(event.target.value)}
          />
        </SettingsRow>
        <SettingsRow label="Role" hint="Administrators can invite people and change roles.">
          <div className="seg" role="group" aria-label="Role">
            {(['member', 'admin'] as UserRole[]).map((value) => (
              <button
                key={value}
                type="button"
                className={role === value ? 'is-on' : undefined}
                aria-pressed={role === value}
                onClick={() => setRole(value)}
              >
                {value === 'admin' ? 'Administrator' : 'Member'}
              </button>
            ))}
          </div>
        </SettingsRow>
        <SettingsRow label="Note" hint="For your own list. The invitee never sees it.">
          <input
            className="input"
            type="text"
            value={note}
            placeholder="Bids desk"
            onChange={(event) => setNote(event.target.value)}
          />
        </SettingsRow>
        <div className="acct__actions">
          <button type="submit" className="btn btn--primary btn--sm" disabled={busy}>
            {busy ? 'Creating…' : 'Create invitation'}
          </button>
          {error ? (
            <span className="acct__err" role="alert">
              {error}
            </span>
          ) : null}
        </div>
      </form>

      {pending.length > 0 ? (
        <ul className="invite__list">
          {pending.map((invite) => (
            <li key={invite.id}>
              <span>
                <b>{invite.email ?? 'Anyone with the link'}</b>
                <span className="badge badge--grey">
                  {invite.role === 'admin' ? 'Administrator' : 'Member'}
                </span>
                {invite.note ? <span className="muted">{invite.note}</span> : null}
              </span>
              <span className="muted">Expires {formatDateTime(invite.expires_at)}</span>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => void revoke(invite.id)}
              >
                Withdraw
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="acct__empty">No invitations are outstanding.</p>
      )}

      {past.length > 0 ? (
        <details className="invite__past">
          <summary>{past.length} used, expired or withdrawn</summary>
          <ul className="invite__list">
            {past.map((invite) => (
              <li key={invite.id}>
                <span>
                  <b>{invite.email ?? 'Anyone with the link'}</b>
                  <span className="badge badge--grey">{invite.status}</span>
                </span>
                <span className="muted">
                  {invite.accepted_at
                    ? `Used ${formatDateTime(invite.accepted_at)}`
                    : `Created ${formatDateTime(invite.created_at)}`}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </SettingsSection>
  );
}

/**
 * Everyone with an account, and the two changes an administrator can make.
 *
 * Both refusals that protect the deployment live on the server (the last
 * administrator cannot be demoted or deactivated, and nobody can deactivate
 * themselves). The controls here are disabled in the same cases purely so the
 * reason is legible before the click rather than after it.
 */
function PeopleSection({
  users,
  auth,
  onChanged,
  error,
}: {
  users: User[];
  auth: Auth;
  onChanged: () => void;
  error: string | null;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  // Which row's password form is open, and what is in it. One at a time: this is
  // a deliberate act aimed at one person, and a grid of open password boxes
  // invites putting the right password on the wrong row.
  const [settingId, setSettingId] = useState<number | null>(null);
  const [password, setPassword] = useState('');
  const [done, setDone] = useState<string | null>(null);

  const activeAdmins = users.filter((user) => user.role === 'admin' && user.is_active).length;

  const givePassword = async (user: User, event: FormEvent) => {
    event.preventDefault();
    setBusyId(user.id);
    setFailure(null);
    setDone(null);
    try {
      const { revoked } = await authApi.setUserPassword(user.id, password);
      setSettingId(null);
      setPassword('');
      // Says what it did to them, not just that it worked. An administrator who
      // does not know they have just signed somebody out will hear about it from
      // that person instead.
      setDone(
        revoked === 0
          ? `${user.email} can now sign in with that password. Send it to them.`
          : `${user.email} can now sign in with that password, and ${revoked} ${revoked === 1 ? 'session was' : 'sessions were'} signed out. Send it to them.`,
      );
      onChanged();
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : 'Could not set that password.');
    } finally {
      setBusyId(null);
    }
  };

  const change = async (user: User, body: { role?: UserRole; is_active?: boolean }) => {
    setBusyId(user.id);
    setFailure(null);
    try {
      const updated = await authApi.updateUser(user.id, body);
      // Keep the sidebar chip honest if an admin demoted themselves.
      if (auth.user && updated.id === auth.user.id) auth.setUser(updated);
      onChanged();
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : 'Could not apply that change.');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <SettingsSection
      title="People"
      note="Everyone who has an account on this dashboard. Anybody marked no password can only get in with their access link — set one for them and signing out stops being a lockout."
    >
      {error ? (
        <p className="acct__err" role="alert">
          {error}
        </p>
      ) : null}
      <ul className="people">
        {users.map((user) => {
          const isSelf = auth.user?.id === user.id;
          const lastAdmin = user.role === 'admin' && user.is_active && activeAdmins <= 1;
          return (
            <li key={user.id} className={user.is_active ? undefined : 'is-off'}>
              <span className="people__who">
                <b>{user.display_name}</b>
                <span className="muted">{user.email}</span>
                {user.has_password ? null : (
                  // Never colour alone (DESIGN.md rule 3): the words are the
                  // status and the tint only draws the eye to them.
                  <span className="badge badge--amber">No password</span>
                )}
              </span>
              <span className="people__when muted">
                {user.last_login_at
                  ? `Last signed in ${formatDateTime(user.last_login_at)}`
                  : 'Never signed in'}
              </span>
              <span className="people__role">
                <div className="seg seg--sm" role="group" aria-label={`Role for ${user.email}`}>
                  {(['member', 'admin'] as UserRole[]).map((value) => (
                    <button
                      key={value}
                      type="button"
                      className={user.role === value ? 'is-on' : undefined}
                      aria-pressed={user.role === value}
                      disabled={busyId === user.id || (lastAdmin && value === 'member')}
                      title={
                        lastAdmin && value === 'member'
                          ? 'This is the only administrator. Promote someone else first.'
                          : undefined
                      }
                      onClick={() => void change(user, { role: value })}
                    >
                      {value === 'admin' ? 'Admin' : 'Member'}
                    </button>
                  ))}
                </div>
              </span>
              <button
                type="button"
                className={`btn btn--sm${user.is_active ? ' btn--ghost' : ''}`}
                disabled={busyId === user.id || (user.is_active && (isSelf || lastAdmin))}
                title={
                  isSelf
                    ? 'You cannot deactivate your own account.'
                    : lastAdmin && user.is_active
                      ? 'This is the only administrator.'
                      : user.is_active
                        ? 'Ends their sessions and refuses further sign-ins'
                        : 'Let them sign in again'
                }
                onClick={() => void change(user, { is_active: !user.is_active })}
              >
                {user.is_active ? 'Deactivate' : 'Reactivate'}
              </button>
              {settingId === user.id ? (
                <form className="people__pw" onSubmit={(event) => void givePassword(user, event)}>
                  <input
                    className="input"
                    type="text"
                    required
                    autoFocus
                    value={password}
                    placeholder={`New password for ${user.email}`}
                    autoComplete="new-password"
                    aria-label={`New password for ${user.email}`}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                  <button
                    type="submit"
                    className="btn btn--sm btn--primary"
                    disabled={busyId === user.id || !password}
                  >
                    {busyId === user.id ? 'Setting…' : 'Set it'}
                  </button>
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost"
                    onClick={() => {
                      setSettingId(null);
                      setPassword('');
                    }}
                  >
                    Cancel
                  </button>
                  <span className="muted">
                    Ends every session they have, including one they are reading on.
                  </span>
                </form>
              ) : (
                <button
                  type="button"
                  className="btn btn--sm btn--ghost people__pwopen"
                  onClick={() => {
                    setSettingId(user.id);
                    setPassword('');
                    setDone(null);
                  }}
                  title={
                    user.has_password
                      ? 'Replace their password. Ends every session they have.'
                      : 'Give them a password so signing out is not a lockout'
                  }
                >
                  {user.has_password ? 'Reset password' : 'Set password'}
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {done ? <p className="acct__ok">{done}</p> : null}
      {failure ? (
        <p className="acct__err" role="alert">
          {failure}
        </p>
      ) : null}
    </SettingsSection>
  );
}
