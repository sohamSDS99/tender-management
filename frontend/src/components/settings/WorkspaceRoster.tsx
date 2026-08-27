import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ApiError, auth as authApi } from '../../api/client';
import { formatDateTime } from '../../labels';
import type { RosterView, UserRole } from '../../types';
import { Icon } from '../Icon';
import { SettingsRow, SettingsSection } from './SettingsPage';

/**
 * Who belongs in this workspace, and the one link they all use (D28).
 *
 * This is the panel that replaced a clerical task. The invitation flow below it
 * needed an administrator to issue a token per person, copy it before the box
 * closed, and paste it somewhere — repeated for every colleague. Here they write
 * down who belongs once, send everybody the same link, and each person lets
 * themselves in.
 *
 * **The link is shown openly and can be read back at any time**, which is the
 * opposite of how an invitation token is treated three sections down. That is
 * not an inconsistency: an invite token *is* the permission, so it is hashed and
 * shown once. This link is not — the roster is. On its own it opens nothing, and
 * the only people it helps are people already listed. Which is exactly what
 * makes it safe to paste into a team channel.
 *
 * The order on screen follows the order of the work: the link first, because
 * that is what an administrator usually came for; then the list; then adding to
 * it.
 */
export function WorkspaceRoster() {
  const [view, setView] = useState<RosterView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setView(await authApi.roster());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not read the workspace list.');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const rotate = async () => {
    setBusy(true);
    setError(null);
    try {
      await authApi.rotateJoinLink();
      setCopied(false);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not create the link.');
    } finally {
      setBusy(false);
    }
  };

  const copy = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      // Clipboard access needs a secure context and fails routinely on the
      // plain-HTTP deployment. The link is on screen and selectable either way,
      // so this must not look like a fault.
      setCopied(false);
    }
  };

  const remove = async (id: number) => {
    try {
      await authApi.removeFromRoster(id);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not remove that address.');
    }
  };

  const setRole = async (id: number, role: UserRole) => {
    try {
      await authApi.setRosterRole(id, role);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not change that role.');
    }
  };

  return (
    <>
      <SettingsSection
        title="Join link"
        note="One link for everyone on the list below. It only works for those addresses, so it is safe to post in a team channel."
      >
        {view?.join_url ? (
          <>
            <div className="invite__link">
              <input
                className="input mono"
                readOnly
                value={view.join_url}
                onFocus={(event) => event.target.select()}
              />
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => void copy(view.join_url!)}
              >
                <Icon name="copy" size={13} />
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
            <div className="acct__actions">
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                disabled={busy}
                onClick={() => void rotate()}
                title="Replaces this link. Anyone who has already joined is unaffected."
              >
                <Icon name="refresh" size={13} />
                {busy ? 'Replacing…' : 'Replace link'}
              </button>
              <span className="muted">
                Replace it if it has been shared too widely. People who have already joined keep
                their accounts.
              </span>
            </div>
          </>
        ) : (
          <div className="acct__actions">
            <button
              type="button"
              className="btn btn--primary btn--sm"
              disabled={busy}
              onClick={() => void rotate()}
            >
              {busy ? 'Creating…' : 'Create join link'}
            </button>
            <span className="muted">No link exists yet.</span>
          </div>
        )}
        {error ? (
          <p className="acct__err" role="alert">
            {error}
          </p>
        ) : null}
      </SettingsSection>

      <SettingsSection
        title="Workspace members"
        note="Only these addresses can create an account. Everyone else is turned away, whatever link they hold."
      >
        {view === null ? (
          <p className="acct__empty">Reading…</p>
        ) : view.entries.length === 0 ? (
          <p className="acct__empty">
            Nobody is on the list yet. Add addresses below, then send them the join link.
          </p>
        ) : (
          <>
            <p className="roster__counts muted">
              {view.total} {view.total === 1 ? 'address' : 'addresses'} · {view.joined} joined ·{' '}
              {view.waiting} not yet
            </p>
            <ul className="people">
              {view.entries.map((entry) => (
                <li key={entry.id} className={entry.joined_at ? undefined : 'is-waiting'}>
                  <span className="people__who">
                    <b>{entry.email}</b>
                    <span className="muted">
                      {entry.joined_at
                        ? `Joined ${formatDateTime(entry.joined_at)}`
                        : 'Has not joined yet'}
                      {entry.note ? ` · ${entry.note}` : ''}
                    </span>
                  </span>
                  <span className="people__role">
                    <div
                      className="seg seg--sm"
                      role="group"
                      aria-label={`Role on joining for ${entry.email}`}
                    >
                      {(['member', 'admin'] as UserRole[]).map((value) => (
                        <button
                          key={value}
                          type="button"
                          className={entry.role === value ? 'is-on' : undefined}
                          aria-pressed={entry.role === value}
                          // Disabled once they have joined, because it would do
                          // nothing: the role on an existing account is changed
                          // under People, and a control that silently has no
                          // effect is worse than one that is visibly off.
                          disabled={entry.joined_at !== null}
                          title={
                            entry.joined_at
                              ? 'They have joined. Change their role under People.'
                              : undefined
                          }
                          onClick={() => void setRole(entry.id, value)}
                        >
                          {value === 'admin' ? 'Admin' : 'Member'}
                        </button>
                      ))}
                    </div>
                  </span>
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost"
                    onClick={() => void remove(entry.id)}
                    title={
                      entry.joined_at
                        ? 'Removes them from the list. Their account stays — close it under People.'
                        : 'They will no longer be able to join'
                    }
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        <AddAddresses onAdded={load} />
      </SettingsSection>
    </>
  );
}

/**
 * The paste box.
 *
 * A textarea rather than one address at a time, because the list already exists
 * somewhere — a mail client's To: field, a spreadsheet column, a Slack message —
 * and retyping it one row at a time is the friction this feature exists to
 * remove. The server splits on commas, semicolons, spaces and newlines.
 */
function AddAddresses({ onAdded }: { onAdded: () => Promise<void> }) {
  const [addresses, setAddresses] = useState('');
  const [role, setRole] = useState<UserRole>('member');
  const [note, setNote] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const result = await authApi.addToRoster({ addresses, role, note: note.trim() });
      setAddresses('');
      setNote('');
      // Both halves are reported. "Added 1" alone would leave somebody who
      // pasted a team list wondering what happened to the other nine.
      const added = result.added.length;
      const skipped = result.already_present.length;
      setStatus(
        [
          added > 0 ? `Added ${added}.` : 'Nothing new to add.',
          skipped > 0 ? `${skipped} already on the list.` : '',
        ]
          .filter(Boolean)
          .join(' '),
      );
      await onAdded();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not add those addresses.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="roster__add" onSubmit={submit}>
      <SettingsRow
        label="Add addresses"
        hint="Paste as many as you like — separated by commas, spaces or new lines."
      >
        <textarea
          className="input roster__paste"
          rows={3}
          value={addresses}
          placeholder={'tanjir@sdsmanager.com\nsomeone.else@sdsmanager.com'}
          onChange={(event) => setAddresses(event.target.value)}
        />
      </SettingsRow>
      <SettingsRow
        label="Role on joining"
        hint="Applies to everyone in this paste. Administrators can invite people and change roles."
      >
        <div className="seg" role="group" aria-label="Role on joining">
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
      <SettingsRow label="Note" hint="For your own list. Nobody else sees it.">
        <input
          className="input"
          type="text"
          value={note}
          placeholder="Bids desk"
          onChange={(event) => setNote(event.target.value)}
        />
      </SettingsRow>
      <div className="acct__actions">
        <button
          type="submit"
          className="btn btn--primary btn--sm"
          disabled={busy || !addresses.trim()}
        >
          {busy ? 'Adding…' : 'Add to workspace'}
        </button>
        {status ? <span className="acct__ok">{status}</span> : null}
        {error ? (
          <span className="acct__err" role="alert">
            {error}
          </span>
        ) : null}
      </div>
    </form>
  );
}
