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
 *
 * **The role is not a footnote on this panel any more (D30).** It decides where
 * the link *lands* its holder — an administrator's opens the dashboard directly,
 * a member's shows the accept screen — so three things change:
 *
 * * the role must be chosen before anything is added, because a link is minted
 *   in the same act and a link whose behaviour nobody chose is not wanted
 * * every row states where its link lands, in words, beside the link itself
 * * re-roling somebody who has not joined **withdraws** their link rather than
 *   quietly repointing one that has already been sent
 */
export function WorkspaceRoster() {
  const [view, setView] = useState<RosterView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  // Whose link this panel just withdrew by re-roling them. Per row rather than
  // one banner, because the reason belongs beside the row it happened to: an
  // administrator who re-roles two people needs to know which link to re-send,
  // and a message at the top of the panel does not tell them.
  const [reRoled, setReRoled] = useState<number | null>(null);

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
      note="Set the address and the role, then send that person their link. An administrator's link opens the dashboard directly; a member's shows an accept screen first. Neither needs a password."
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
          Nobody is on the list yet. Add addresses below, choose what each batch will be, and every
          row comes back with a link to send.
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
                              : entry.access_url
                                ? 'Changes where their link lands them, so the current link is withdrawn'
                                : undefined
                          }
                          onClick={() => {
                            // Recorded before the call, not after: `act` reloads
                            // the panel and the row that comes back has no link
                            // on it. Without the note that reads as a link which
                            // vanished on its own.
                            setReRoled(entry.access_url ? entry.id : null);
                            void act(entry.id, () => authApi.setRosterRole(entry.id, value));
                          }}
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
                  <>
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
                    {/*
                      Said in words, beside the link, because the difference is
                      invisible in the URL — the two are the same shape and land
                      in different places.

                      Only claimed for somebody who has not joined. Once they
                      have an account the landing follows *that* role, which this
                      row does not know: an administrator may have promoted them
                      under People, and the entry still says what it always said.
                    */}
                    <p className="roster__lands muted">
                      {entry.joined_at
                        ? 'Signs them in again whenever they open it.'
                        : entry.role === 'admin'
                          ? 'Opens the dashboard directly — nothing to accept.'
                          : 'Shows the accept screen, then the dashboard.'}
                    </p>
                  </>
                ) : (
                  <div className="acct__actions roster__link">
                    <button
                      type="button"
                      className="btn btn--sm"
                      disabled={busyId === entry.id}
                      onClick={() => void act(entry.id, () => authApi.issueAccessLink(entry.id))}
                    >
                      <Icon name="refresh" size={13} />
                      Generate link
                    </button>
                    <span className="muted">
                      {reRoled === entry.id
                        ? `Role changed to ${entry.role === 'admin' ? 'administrator' : 'member'}, so the old link was withdrawn. Generate a new one and send that.`
                        : 'No link — revoked, or never issued.'}
                    </span>
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
 * The paste box: addresses, then a role, then the links.
 *
 * A textarea rather than one address at a time, because the list already exists
 * somewhere — a mail client's To: field, a spreadsheet column, a Slack message —
 * and retyping it row by row is the friction this feature removes. The server
 * splits on commas, semicolons, spaces and newlines, and mints every link as it
 * goes so the whole batch is ready to send.
 *
 * **The role starts unset and the button stays disabled until it is chosen
 * (D30).** It used to default to Member, which meant an administrator in a hurry
 * could generate a batch of links without ever making the decision that now
 * determines where those links *land* people. One unpicked control is a cheaper
 * way to enforce "addresses and roles first, links second" than any amount of
 * copy, and the API refuses a request with no role for the same reason.
 *
 * One role per paste rather than one per address, deliberately: two roles in one
 * box means parsing a format nobody has, and the ordinary case is a batch of
 * colleagues who are all the same thing. Mixed teams are two pastes.
 */
function AddAddresses({ onAdded }: { onAdded: () => Promise<void> }) {
  const [addresses, setAddresses] = useState('');
  const [role, setRole] = useState<UserRole | null>(null);
  const [note, setNote] = useState('');
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
      const result = await authApi.addToRoster({ addresses, role, note: note.trim() });
      setAddresses('');
      setNote('');
      const added = result.added.length;
      const skipped = result.already_present.length;
      // Both halves reported. "Added 1" alone would leave somebody who pasted a
      // team list wondering what happened to the other nine.
      setStatus(
        [
          added > 0
            ? `Added ${added} as ${role === 'admin' ? 'administrator' : 'member'}${added === 1 ? '' : 's'}, each with a link above. Send each person theirs.`
            : 'Nothing new to add.',
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
        hint="Required, and it applies to everyone in this paste. A member's link shows them an accept screen first; an administrator's opens the dashboard directly, and lets them add people and change roles."
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
          disabled={busy || !addresses.trim() || role === null}
          // Disabled buttons cannot say why on hover in every browser, so the
          // reason is on the wrapper and reachable either way.
          title={role === null ? 'Choose a role first' : undefined}
        >
          {busy ? 'Adding…' : 'Add and generate links'}
        </button>
        {role === null && addresses.trim() ? (
          <span className="muted">Choose Member or Administrator to generate their links.</span>
        ) : null}
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
