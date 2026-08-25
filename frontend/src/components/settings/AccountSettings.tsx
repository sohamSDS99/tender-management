import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ApiError, auth as authApi } from '../../api/client';
import { describeAgent, initials, type Auth } from '../../state/auth';
import { formatDateTime } from '../../labels';
import type { AuthSession } from '../../types';
import { Icon } from '../Icon';
import { SettingsPage, SettingsRow, SettingsSection } from './SettingsPage';
import { TeamAdmin } from './TeamAdmin';

/**
 * The profile: who you are, your password, and the browsers you are signed in
 * on. Administrators also get the invitation and people sections below it.
 *
 * Signed out, this page is not an error and not a redirect — it is the one
 * place in the dashboard that explains what an account is *for* here, which
 * matters because nothing on any other page requires one (D25). Somebody who
 * lands here from the sidebar deserves an answer better than a locked door.
 */
export function AccountSettings({ auth, onBack }: { auth: Auth; onBack: () => void }) {
  if (!auth.user) {
    return (
      <SettingsPage
        title="Account"
        blurb="Sign in to keep a profile. Everything else on this dashboard works without one."
        onBack={onBack}
      >
        <SettingsSection
          title="You are not signed in"
          note="Tenders, filters, sweeps and settings are all open to anyone who can reach this page. An account adds a profile and, for administrators, the ability to invite people."
        >
          <p className="acct__empty">
            {auth.bootstrap
              ? 'No account exists on this dashboard yet. The first one created becomes the administrator.'
              : 'New accounts are by invitation. Ask an administrator for a link.'}
          </p>
        </SettingsSection>
      </SettingsPage>
    );
  }

  return (
    <SettingsPage
      title="Account"
      blurb="Your profile, your password, and the browsers you are signed in on."
      onBack={onBack}
    >
      <ProfileSection auth={auth} />
      <PasswordSection />
      <SessionsSection />
      {auth.user.role === 'admin' ? <TeamAdmin auth={auth} /> : null}
    </SettingsPage>
  );
}

/** Name and email, plus the facts about the account that nobody can edit. */
function ProfileSection({ auth }: { auth: Auth }) {
  const user = auth.user!;
  const [displayName, setDisplayName] = useState(user.display_name);
  const [email, setEmail] = useState(user.email);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const dirty = displayName !== user.display_name || email !== user.email;

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const updated = await authApi.updateProfile({ display_name: displayName, email });
      // Push the new user into the hook so the sidebar chip changes with the
      // form, rather than staying stale until the next page load.
      auth.setUser(updated);
      setDisplayName(updated.display_name);
      setEmail(updated.email);
      setStatus('Saved.');
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not save that.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsSection title="Profile" note="How you appear to anyone else with an account.">
      <div className="acct__identity">
        <span className="acct__avatar" aria-hidden="true">
          {initials(user)}
        </span>
        <div>
          <p className="acct__name">{user.display_name}</p>
          <p className="acct__meta">
            <span className={`badge ${user.role === 'admin' ? 'badge--green' : 'badge--grey'}`}>
              {user.role === 'admin' ? 'Administrator' : 'Member'}
            </span>
            <span className="muted">Joined {formatDateTime(user.created_at)}</span>
            {user.last_login_at ? (
              <span className="muted">Last signed in {formatDateTime(user.last_login_at)}</span>
            ) : null}
          </p>
        </div>
      </div>

      <form onSubmit={save}>
        <SettingsRow label="Display name" hint="Leave it blank and your email name is used.">
          <input
            className="input"
            type="text"
            value={displayName}
            autoComplete="name"
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </SettingsRow>
        <SettingsRow label="Email" hint="This is also how you sign in.">
          <input
            className="input"
            type="email"
            value={email}
            autoComplete="username"
            onChange={(event) => setEmail(event.target.value)}
          />
        </SettingsRow>
        <div className="acct__actions">
          <button type="submit" className="btn btn--primary btn--sm" disabled={busy || !dirty}>
            {busy ? 'Saving…' : 'Save changes'}
          </button>
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

/**
 * Changing a password, and saying what that did to the other sessions.
 *
 * The count is reported rather than swallowed because "password changed" alone
 * leaves the question the person actually had — is the machine I am worried
 * about still signed in? — unanswered.
 */
function PasswordSection() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const { revoked } = await authApi.changePassword(current, next);
      setCurrent('');
      setNext('');
      setStatus(
        revoked === 0
          ? 'Password changed. No other browsers were signed in.'
          : `Password changed. ${revoked} other ${revoked === 1 ? 'session was' : 'sessions were'} signed out.`,
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not change it.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsSection
      title="Password"
      note="Changing it signs out every other browser. This one stays signed in."
    >
      <form onSubmit={save}>
        <SettingsRow label="Current password">
          <input
            className="input"
            type="password"
            required
            value={current}
            autoComplete="current-password"
            onChange={(event) => setCurrent(event.target.value)}
          />
        </SettingsRow>
        <SettingsRow label="New password" hint="At least 10 characters.">
          <input
            className="input"
            type="password"
            required
            value={next}
            autoComplete="new-password"
            onChange={(event) => setNext(event.target.value)}
          />
        </SettingsRow>
        <div className="acct__actions">
          <button
            type="submit"
            className="btn btn--primary btn--sm"
            disabled={busy || !current || !next}
          >
            {busy ? 'Changing…' : 'Change password'}
          </button>
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

/** Live sessions, and the one button that ends all of them but this one. */
function SessionsSection() {
  const [rows, setRows] = useState<AuthSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setRows(await authApi.sessions());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not read your sessions.');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const signOutOthers = async () => {
    setBusy(true);
    setError(null);
    try {
      await authApi.signOutOthers();
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not sign the others out.');
    } finally {
      setBusy(false);
    }
  };

  const others = (rows ?? []).filter((row) => !row.current).length;

  return (
    <SettingsSection
      title="Signed-in browsers"
      note="Every browser currently holding a session for your account."
    >
      {rows === null ? (
        <p className="acct__empty">Reading…</p>
      ) : (
        <ul className="acct__sessions">
          {rows.map((row) => (
            <li key={row.id} className={row.current ? 'is-current' : undefined}>
              <span className="acct__agent" title={row.user_agent || undefined}>
                {describeAgent(row.user_agent)}
                {row.current ? <b> — this browser</b> : null}
              </span>
              <span className="muted">
                Last active {formatDateTime(row.last_seen_at)} · expires{' '}
                {formatDateTime(row.expires_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="acct__actions">
        <button
          type="button"
          className="btn btn--sm"
          disabled={busy || others === 0}
          onClick={() => void signOutOthers()}
          title={
            others === 0
              ? 'No other browsers are signed in'
              : 'End every session except this browser'
          }
        >
          <Icon name="block" size={13} />
          {busy ? 'Signing out…' : 'Sign out everywhere else'}
        </button>
        {error ? (
          <span className="acct__err" role="alert">
            {error}
          </span>
        ) : null}
      </div>
    </SettingsSection>
  );
}
