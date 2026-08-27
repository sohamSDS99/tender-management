import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ApiError, auth as authApi } from '../../api/client';
import { formatDateTime } from '../../labels';
import type { RosterView, UserRole } from '../../types';
import { Icon } from '../Icon';
import { SettingsRow, SettingsSection } from './SettingsPage';

/**
 * Who belongs in this workspace, and each person's personal link (D29).
 *
 * The workflow this replaced was clerical: issue a token per person, copy it
 * before the box closed, paste it, repeat — and the person on the other end then
 * had to invent a password. Here an administrator pastes addresses once, and
 * every row carries a ready-to-send link that is the whole of what its owner
 * needs. They open it, press one button, and they are in.
 *
 * **Every link on this page is a live credential**, which is why the panel is
 * administrators-only and why each row offers Revoke. Whoever holds somebody's
 * link is that person, so the copy says so plainly rather than leaving an
 * administrator to infer it from a URL that looks like any other.
 *
 * Links are shown rather than hidden behind a reveal. An administrator who
 * cannot see them cannot send them, and hiding a credential from the one person
 * entitled to it buys nothing.
 */
export function WorkspaceRoster() {
  const [view, setView] = useState<RosterView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

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

  const act = async (id: number, run: () => Promise<unknown>) => {
    setBusyId(id);
    setError(null);
    try {
      await run();
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'That did not work.');
    } finally {
      setBusyId(null);
    }
  };

  const copy = async (id: number, url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopiedId(id);
    } catch {
      // Clipboard access needs a secure context and fails routinely on a
      // plain-HTTP deployment. The link is on screen and selectable either way,
      // so this must not look like a fault.
      setCopiedId(null);
    }
  };

  return (
    <SettingsSection
      title="Workspace members"
      note="Everyone here gets their own link. Opening it is all they have to do — there is no password. Send each person theirs."
    >
      {error ? (
        <p className="acct__err" role="alert">
          {error}
        </p>
      ) : null}

      {view === null ? (
        <p className="acct__empty">Reading…</p>
      ) : view.entries.length === 0 ? (
        <p className="acct__empty">
          Nobody is on the list yet. Add addresses below and each one gets a link to send.
        </p>
      ) : (
        <>
          <p className="roster__counts muted">
            {view.total} {view.total === 1 ? 'person' : 'people'} · {view.joined} joined ·{' '}
            {view.waiting} not yet
          </p>
          <ul className="roster">
            {view.entries.map((entry) => (
              <li key={entry.id} className={entry.joined_at ? 'is-joined' : undefined}>
                <div className="roster__head">
                  <span className="roster__who">
                    <b>{entry.email}</b>
                    <span className="muted">
                      {entry.joined_at
                        ? `Joined ${formatDateTime(entry.joined_at)}`
                        : 'Not joined yet'}
                      {entry.note ? ` · ${entry.note}` : ''}
                    </span>
                  </span>
                  <span className="roster__role">
                    <div
                      className="seg seg--sm"
                      role="group"
                      aria-label={`Role for ${entry.email}`}
                    >
                      {(['member', 'admin'] as UserRole[]).map((value) => (
                        <button
                          key={value}
                          type="button"
                          className={entry.role === value ? 'is-on' : undefined}
                          aria-pressed={entry.role === value}
                          // Once they have joined this would do nothing — the
                          // role lives on the account now, and is changed under
                          // People. A control that silently has no effect is
                          // worse than one that is visibly unavailable.
                          disabled={entry.joined_at !== null || busyId === entry.id}
                          title={
                            entry.joined_at
                              ? 'They have joined. Change their role under People.'
                              : undefined
                          }
                          onClick={() =>
                            void act(entry.id, () => authApi.setRosterRole(entry.id, value))
                          }
                        >
                          {value === 'admin' ? 'Admin' : 'Member'}
                        </button>
                      ))}
                    </div>
                  </span>
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost"
                    disabled={busyId === entry.id}
                    onClick={() => void act(entry.id, () => authApi.removeFromRoster(entry.id))}
                    title={
                      entry.joined_at
                        ? 'Takes them off the list. Their account stays — close it under People.'
                        : 'They will no longer be able to join'
                    }
                  >
                    Remove
                  </button>
                </div>

                {entry.access_url ? (
                  <div className="invite__link roster__link">
                    <input
                      className="input mono"
                      readOnly
                      value={entry.access_url}
                      onFocus={(event) => event.target.select()}
                    />
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => void copy(entry.id, entry.access_url!)}
                    >
                      <Icon name="copy" size={13} />
                      {copiedId === entry.id ? 'Copied' : 'Copy'}
                    </button>
                    <button
                      type="button"
                      className="btn btn--sm btn--ghost"
                      disabled={busyId === entry.id}
                      onClick={() => void act(entry.id, () => authApi.revokeAccessLink(entry.id))}
                      title="Stops this link working. They keep any session they already have."
                    >
                      Revoke
                    </button>
                  </div>
                ) : (
                  <div className="acct__actions roster__link">
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={busyId === entry.id}
                      onClick={() => void act(entry.id, () => authApi.issueAccessLink(entry.id))}
                    >
                      <Icon name="refresh" size={13} />
                      New link
                    </button>
                    <span className="muted">No link — revoked, or never issued.</span>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      <AddAddresses onAdded={load} />
    </SettingsSection>
  );
}

/**
 * The paste box.
 *
 * A textarea rather than one address at a time, because the list already exists
 * somewhere — a mail client's To: field, a spreadsheet column, a Slack message —
 * and retyping it row by row is the friction this feature removes. The server
 * splits on commas, semicolons, spaces and newlines, and mints every link as it
 * goes so the whole batch is ready to send.
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
      const added = result.added.length;
      const skipped = result.already_present.length;
      // Both halves reported. "Added 1" alone would leave somebody who pasted a
      // team list wondering what happened to the other nine.
      setStatus(
        [
          added > 0 ? `Added ${added}, each with a link above.` : 'Nothing new to add.',
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
        label="Add people"
        hint="Paste as many addresses as you like — commas, spaces or new lines. Each gets its own link."
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
        label="Role"
        hint="Applies to everyone in this paste. Administrators can add people and change roles."
      >
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
