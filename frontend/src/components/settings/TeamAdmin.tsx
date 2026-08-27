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
        Order follows how the work actually happens: the workspace list and its
        shared link first, because that is the ordinary way somebody joins;
        single-use invitations second, kept for the outsider who is not on the
        list at all; then everyone who already has an account.
      */}
      <WorkspaceRoster />
      <InviteSection invites={invites} onChanged={load} />
      <PeopleSection users={users} auth={auth} onChanged={load} error={error} />
    </>
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

  const activeAdmins = users.filter((user) => user.role === 'admin' && user.is_active).length;

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
    <SettingsSection title="People" note="Everyone who has an account on this dashboard.">
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
            </li>
          );
        })}
      </ul>
      {failure ? (
        <p className="acct__err" role="alert">
          {failure}
        </p>
      ) : null}
    </SettingsSection>
  );
}
