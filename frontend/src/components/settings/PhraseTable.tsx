import { useMemo, useState } from 'react';
import type { MatchingProfile } from '../../types';
import { normalisePhrase } from '../../labels';
import { Icon } from '../Icon';

type Tier = 'strong' | 'medium' | 'weak';

/** What each tier is worth, from the weights block in the relevance file. */
export const TIER_POINTS: Record<Tier, number> = { strong: 26, medium: 12, weak: 5 };
const TIERS: Tier[] = ['strong', 'medium', 'weak'];
const PAGE = 10;

interface Row {
  phrase: string;
  tier: Tier;
}

function rowsOf(profile: MatchingProfile): Row[] {
  return TIERS.flatMap((tier) => profile[tier].map((phrase) => ({ phrase, tier })));
}

/**
 * Every phrase in one profile, as a table you can add to and take from.
 *
 * Paged at ten because a profile can hold sixty phrases and a wall of them is
 * unreadable — the point of this screen is changing one, not admiring the list.
 */
export function PhraseTable({
  profile,
  onChange,
}: {
  profile: MatchingProfile;
  /** The whole profile after the edit, so the parent holds one source of truth. */
  onChange: (next: MatchingProfile) => void;
}) {
  const [draft, setDraft] = useState('');
  const [draftTier, setDraftTier] = useState<Tier>('strong');
  const [page, setPage] = useState(0);

  const rows = useMemo(() => rowsOf(profile), [profile]);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const shown = rows.slice(page * PAGE, page * PAGE + PAGE);

  const normalised = normalisePhrase(draft);
  const duplicate = rows.some((r) => r.phrase === normalised);
  const canAdd = normalised.length > 1 && !duplicate;

  const write = (next: Row[]) =>
    onChange({
      ...profile,
      strong: next.filter((r) => r.tier === 'strong').map((r) => r.phrase),
      medium: next.filter((r) => r.tier === 'medium').map((r) => r.phrase),
      weak: next.filter((r) => r.tier === 'weak').map((r) => r.phrase),
    });

  const add = () => {
    if (!canAdd) return;
    write([{ phrase: normalised, tier: draftTier }, ...rows]);
    setDraft('');
    setPage(0);
  };

  const remove = (row: Row) => {
    const next = rows.filter((r) => !(r.phrase === row.phrase && r.tier === row.tier));
    write(next);
    const lastPage = Math.max(0, Math.ceil(next.length / PAGE) - 1);
    if (page > lastPage) setPage(lastPage);
  };

  const retier = (row: Row, tier: Tier) =>
    write(rows.map((r) => (r.phrase === row.phrase && r.tier === row.tier ? { ...r, tier } : r)));

  return (
    <div className="phrases">
      <div className="phrases__add">
        <input
          className="input input--sm"
          value={draft}
          placeholder="Add a phrase…"
          aria-label="New phrase"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              add();
            }
          }}
        />
        <select
          className="select select--sm"
          value={draftTier}
          aria-label="Strength of the new phrase"
          onChange={(event) => setDraftTier(event.target.value as Tier)}
        >
          {TIERS.map((tier) => (
            <option key={tier} value={tier}>
              {tier} · {TIER_POINTS[tier]} pts
            </option>
          ))}
        </select>
        <button type="button" className="btn btn--primary btn--sm" disabled={!canAdd} onClick={add}>
          Add phrase
        </button>
      </div>

      {/* Normalisation shown as you type, so the file's matching contract stops
          being a trap: "Cloud-Based" that silently never matches is visible
          before it is saved, not after a re-score. */}
      {draft.trim() ? (
        <p className="phrases__note">
          {duplicate ? (
            <>Already in this profile as “{normalised}”.</>
          ) : (
            <>
              Stored as <span className="mono">{normalised || '—'}</span>
            </>
          )}
        </p>
      ) : null}

      <table className="ptable">
        <thead>
          <tr>
            <th scope="col">Phrase</th>
            <th scope="col">Strength</th>
            <th scope="col" className="ptable__act">
              <span className="sr">Remove</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {shown.length === 0 ? (
            <tr>
              <td colSpan={3} className="ptable__empty">
                No phrases yet. Anything added here is matched against every notice.
              </td>
            </tr>
          ) : (
            shown.map((row) => (
              <tr key={`${row.tier}:${row.phrase}`}>
                <td className="mono">{row.phrase}</td>
                <td>
                  <select
                    className="select select--sm"
                    value={row.tier}
                    aria-label={`Strength of ${row.phrase}`}
                    onChange={(event) => retier(row, event.target.value as Tier)}
                  >
                    {TIERS.map((tier) => (
                      <option key={tier} value={tier}>
                        {tier} · {TIER_POINTS[tier]} pts
                      </option>
                    ))}
                  </select>
                </td>
                <td className="ptable__act">
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    aria-label={`Remove phrase: ${row.phrase}`}
                    onClick={() => remove(row)}
                  >
                    <Icon name="close" size={13} />
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      {pages > 1 ? (
        <div className="phrases__pager">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
          >
            Previous
          </button>
          <span>
            {rows.length} phrases · page {page + 1} of {pages}
          </span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={page >= pages - 1}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      ) : (
        <p className="phrases__pager">
          <span>{rows.length} phrases</span>
        </p>
      )}
    </div>
  );
}
