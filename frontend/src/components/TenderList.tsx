import type { Tender, Verdict } from '../types';
import type { ScoreBands } from '../labels';
import {
  deadlineUrgency,
  deploymentLabel,
  deploymentTone,
  fitLabel,
  countryLabel,
  fitTone,
  formatDate,
  formatValue,
  safeHref,
  scoreTone,
} from '../labels';
import { Icon } from './Icon';

/**
 * One card per notice.
 *
 * The card is clickable and *also* contains a real link to the buyer's original
 * notice. Those two things fight: an anchor cannot live inside a button. So the
 * title is the button and its ::after overlays the whole card as the hit area,
 * while the external link sits above that overlay. Both are genuinely focusable,
 * which a div-with-onClick would not be, and the link genuinely navigates, which
 * a styled span would not.
 *
 * Colour appears only where it carries meaning: the score band, a deadline inside
 * two weeks, a disqualifier. Every coloured thing also says what it is in words.
 */
export interface TenderListProps {
  tenders: Tender[];
  loading: boolean;
  error: string | null;
  /** True only when the API could not be reached at all (status 0). */
  unreachable: boolean;
  selectedId: number | null;
  /** Anything first seen at or after this is marked New. */
  newSince: string | null;
  /** Filters the reader has set, counted against an unconstrained baseline. */
  filterCount: number;
  /** Total matching the current query, which may exceed what this page holds. */
  total: number;
  /** Everything ever stored, so "nothing here" is never confused with "nothing exists". */
  storedTotal: number;
  bands: ScoreBands;
  sourceLabel: (key: string) => string;
  /** Machine keys like "sds_management" have no business on screen. */
  categoryLabel: (key: string) => string;
  onSelect: (id: number) => void;
  onRetry: () => void;
  onClearFilters: () => void;
  onFirstPage: () => void;
  onShowAll: () => void;
  /** Record or withdraw a verdict. `null` withdraws it. */
  onVerdict: (id: number, verdict: Verdict | null) => void;
  /** The notice whose mark is in flight, so its own control can say so. */
  verdictBusy: number | null;
}

const SCORE_CLASS = { green: 'green', amber: 'amber', red: 'red', grey: 'grey' } as const;
const BADGE_CLASS = { green: 'green', amber: 'amber', red: 'red', grey: 'grey' } as const;

function Card({
  tender,
  selected,
  isNew,
  bands,
  sourceLabel,
  categoryLabel,
  onSelect,
  onVerdict,
  verdictBusy,
}: {
  tender: Tender;
  selected: boolean;
  isNew: boolean;
  bands: ScoreBands;
  sourceLabel: (key: string) => string;
  categoryLabel: (key: string) => string;
  onSelect: (id: number) => void;
  onVerdict: (id: number, verdict: Verdict | null) => void;
  verdictBusy: number | null;
}) {
  const { urgency, label } = deadlineUrgency(tender.deadline);
  const band = scoreTone(tender.relevance_score, bands);
  const disqualifier = tender.disqualifiers[0];
  const flag = tender.review_flags[0];
  const reason = tender.relevance_reasons[0];
  const href = safeHref(tender.source_url);
  const verdict = tender.feedback?.verdict ?? null;
  const marking = verdictBusy === tender.id;
  // Only the learner's call needs explaining on the card. A reviewer's own mark
  // needs no justification, and the notice they marked it on is right here.
  const learnedReason = verdict === null ? tender.auto_irrelevant_reasons[0] : undefined;

  const meta = [
    tender.buyer_name,
    countryLabel(tender.buyer_country),
    tender.publication_date ? `published ${formatDate(tender.publication_date)}` : null,
  ].filter(Boolean) as string[];

  return (
    <div className={`rescard${selected ? ' is-selected' : ''}`}>
      <div>
        <span className={`score score--${SCORE_CLASS[band]} num`}>{tender.relevance_score}</span>
      </div>

      <div>
        <h3 className="rescard__title">
          <button type="button" className="rescard__titlebtn" onClick={() => onSelect(tender.id)}>
            {tender.title || 'Untitled notice'}
          </button>
        </h3>

        <div className="badges">
          <span className={`badge badge--${BADGE_CLASS[fitTone(tender.fit_status)]}`}>
            {fitTone(tender.fit_status) === 'green' ? <Icon name="check" size={11} /> : null}
            {fitLabel(tender.fit_status)}
          </span>
          <span className={`badge badge--${BADGE_CLASS[deploymentTone(tender.deployment_fit)]}`}>
            {deploymentLabel(tender.deployment_fit)}
          </span>
          {tender.relevance_category ? (
            <span className="badge badge--line">{categoryLabel(tender.relevance_category)}</span>
          ) : null}
          {isNew ? <span className="badge badge--new">New</span> : null}
          {!tender.is_actionable ? <span className="badge badge--grey">Closed</span> : null}
          {/* Which of the two hid it, said in words rather than by colour: a
              reviewer's decision and a machine's guess at one are different
              things, and only one of them is evidence. */}
          {verdict === 'irrelevant' ? (
            <span className="badge badge--grey">Marked not relevant</span>
          ) : verdict === 'relevant' ? (
            <span className="badge badge--green">
              <Icon name="check" size={11} />
              Marked relevant
            </span>
          ) : tender.auto_irrelevant ? (
            <span className="badge badge--grey">Hidden by learning</span>
          ) : null}
        </div>

        <p className="metaline">
          {meta.map((part, index) => (
            <span key={`${part}-${index}`}>{part}</span>
          ))}
          <span className="mono">{sourceLabel(tender.source)}</span>
        </p>

        {reason ? (
          <p className="rescard__why">
            <Icon name="check" size={13} />
            {reason}
          </p>
        ) : null}
        {learnedReason ? (
          <p className="flagline flagline--flag">
            <Icon name="block" size={13} />
            Hidden because {learnedReason}
          </p>
        ) : disqualifier ? (
          <p className="flagline flagline--bad">
            <Icon name="block" size={13} />
            {disqualifier}
          </p>
        ) : flag ? (
          <p className="flagline flagline--flag">
            <Icon name="warn" size={13} />
            {flag}
          </p>
        ) : null}
      </div>

      <div className="rescard__side">
        <span
          className={`deadline${
            urgency === 'urgent' ? ' deadline--urgent' : urgency === 'soon' ? ' deadline--soon' : ''
          }`}
        >
          <b>{tender.deadline ? formatDate(tender.deadline) : '—'}</b>
          <em>
            {tender.deadline ? <Icon name="clock" size={11} /> : null}
            {label}
          </em>
        </span>
        <span className={`value${tender.estimated_value === null ? ' muted' : ''}`}>
          {formatValue(tender.estimated_value, tender.currency)}
        </span>
        {href ? (
          <a
            className="openlink"
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            // Above the title button's overlay, so this link wins its own clicks.
            onClick={(event) => event.stopPropagation()}
          >
            Original notice
            <Icon name="external" size={11} />
          </a>
        ) : null}

        {/* One button, not two. Rejecting is the frequent act - it is what
            working through a list *is* - while keeping something is rare and
            deliberate, so it lives in the detail panel with the note field
            beside it. Raised above the title's overlay like the link above, or
            the card would swallow the click and open the drawer instead. */}
        {verdict === null ? (
          <button
            type="button"
            className="verdict__btn"
            disabled={marking}
            title="Hide this notice, and teach the system to hide ones like it"
            onClick={(event) => {
              event.stopPropagation();
              onVerdict(tender.id, 'irrelevant');
            }}
          >
            <Icon name="block" size={11} />
            {marking ? 'Marking…' : 'Not relevant'}
          </button>
        ) : (
          <button
            type="button"
            className="verdict__btn"
            disabled={marking}
            title="Withdraw this mark"
            onClick={(event) => {
              event.stopPropagation();
              onVerdict(tender.id, null);
            }}
          >
            <Icon name="refresh" size={11} />
            {marking ? 'Undoing…' : 'Undo'}
          </button>
        )}
      </div>
    </div>
  );
}

function Skeletons() {
  return (
    <div className="list" aria-busy="true">
      <span className="sr">Loading tenders…</span>
      {[68, 54, 74, 61, 47].map((width, index) => (
        <div className="skel" key={index}>
          <div className="sk sk--pill" />
          <div>
            <div className="sk sk--h" style={{ width: `${width}%` }} />
            <div className="sk sk--s" style={{ width: '38%' }} />
          </div>
          <div>
            <div className="sk sk--s" style={{ width: '70%', marginLeft: 'auto' }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function TenderList({
  tenders,
  loading,
  error,
  unreachable,
  selectedId,
  newSince,
  filterCount,
  total,
  storedTotal,
  bands,
  sourceLabel,
  categoryLabel,
  onSelect,
  onRetry,
  onClearFilters,
  onFirstPage,
  onShowAll,
  onVerdict,
  verdictBusy,
}: TenderListProps) {
  if (error) {
    return (
      <div className="state state--error" role="alert">
        <div className="state__icon">
          <Icon name="warn" size={22} />
        </div>
        {/* Only an unreachable API deserves the docker sentence. A rejected
            request or a frontend fault used to be reported as "start the
            backend", which sent the reader after the wrong problem. */}
        <h3>Could not load tenders</h3>
        <p>
          {error}
          {unreachable ? (
            <>
              {' '}
              Start it with <code>docker compose up -d</code>, then retry.
            </>
          ) : null}
        </p>
        <div className="state__actions">
          <button type="button" className="btn btn--primary" onClick={onRetry}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (loading) return <Skeletons />;

  if (tenders.length === 0) {
    // Three genuinely different situations, and saying the wrong one is worse
    // than saying nothing: this page is past the end of a non-empty result set;
    // filters have excluded everything; or the database really is empty.
    const pastEnd = total > 0;
    return (
      <div className="state">
        <div className="state__icon">
          <Icon name="search" size={22} />
        </div>
        <h3>{pastEnd ? 'Nothing on this page' : 'Nothing matches'}</h3>
        <p>
          {pastEnd
            ? `There ${total === 1 ? 'is 1 tender' : `are ${total.toLocaleString('en-GB')} tenders`} in this view, but none on this page.`
            : filterCount > 0
              ? `${filterCount} ${filterCount === 1 ? 'filter is' : 'filters are'} narrowing this down. Clearing them returns you to the default view.`
              : storedTotal > 0
                ? `Nothing in this view matches. ${storedTotal.toLocaleString('en-GB')} tenders are stored — the All stored tab shows every one.`
                : 'Nothing has been stored yet. Start a sweep from the top of the page, or wait for the next scheduled one.'}
        </p>
        {pastEnd || filterCount > 0 || storedTotal > 0 ? (
          <div className="state__actions">
            {pastEnd ? (
              <button type="button" className="btn btn--primary" onClick={onFirstPage}>
                Go to the first page
              </button>
            ) : null}
            {filterCount > 0 ? (
              <button
                type="button"
                className={pastEnd ? 'btn' : 'btn btn--primary'}
                onClick={onClearFilters}
              >
                Clear filters
              </button>
            ) : !pastEnd && storedTotal > 0 ? (
              <button type="button" className="btn btn--primary" onClick={onShowAll}>
                Show all {storedTotal.toLocaleString('en-GB')}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }

  const isNew = (tender: Tender): boolean =>
    Boolean(newSince) && new Date(tender.first_seen_at).getTime() >= new Date(newSince!).getTime();

  return (
    <div className="list">
      {tenders.map((tender) => (
        <Card
          key={tender.id}
          tender={tender}
          selected={tender.id === selectedId}
          isNew={isNew(tender)}
          bands={bands}
          sourceLabel={sourceLabel}
          categoryLabel={categoryLabel}
          onSelect={onSelect}
          onVerdict={onVerdict}
          verdictBusy={verdictBusy}
        />
      ))}
    </div>
  );
}
