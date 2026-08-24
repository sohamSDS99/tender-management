import type { Tender } from '../types';
import {
  deadlineUrgency,
  deploymentLabel,
  deploymentTone,
  fitLabel,
  countryLabel,
  fitTone,
  formatDate,
  formatValue,
  scoreTone,
} from '../labels';
import { Icon } from './Icon';

/**
 * Hairline-separated rows, not cards.
 *
 * Exactly one element per row is loud — the title. Everything else sits at
 * 0.8125rem in a quieter ink, because the reader is scanning titles and deadlines
 * and rejecting most of them. Colour appears only where it carries meaning: the
 * score band, a deadline inside 14 days, a disqualifier.
 */
export interface TenderListProps {
  tenders: Tender[];
  loading: boolean;
  error: string | null;
  selectedId: number | null;
  /** Anything first seen at or after this is marked New. */
  newSince: string | null;
  filterCount: number;
  /** Total matching the current query, which may exceed what this page holds. */
  total: number;
  onSelect: (id: number) => void;
  onRetry: () => void;
  onClearFilters: () => void;
  onFirstPage: () => void;
}

const TONE_CLASS = { green: 'good', amber: 'warn', red: 'bad', grey: 'flat' } as const;

function Row({
  tender,
  selected,
  isNew,
  onSelect,
}: {
  tender: Tender;
  selected: boolean;
  isNew: boolean;
  onSelect: (id: number) => void;
}) {
  const { urgency, label } = deadlineUrgency(tender.deadline);
  const band = scoreTone(tender.relevance_score);
  const disqualifier = tender.disqualifiers[0];
  const flag = tender.review_flags[0];
  const reason = tender.relevance_reasons[0];

  const meta = [
    tender.buyer_name,
    countryLabel(tender.buyer_country),
    tender.source,
    tender.publication_date ? `published ${formatDate(tender.publication_date)}` : null,
  ].filter(Boolean) as string[];

  return (
    <button
      type="button"
      className={`row${selected ? ' is-on' : ''}`}
      onClick={() => onSelect(tender.id)}
      aria-label={`${tender.title}. Score ${tender.relevance_score} of 100. ${fitLabel(
        tender.fit_status,
      )}.`}
    >
      <span className={`score score--${TONE_CLASS[band]}`}>
        <span className="score__n">{tender.relevance_score}</span>
        <span className="score__bar">
          <i style={{ width: `${Math.max(4, tender.relevance_score)}%` }} />
        </span>
      </span>

      <span>
        <span className="row__title">{tender.title || 'Untitled notice'}</span>

        {/* Our classification first: it is what the reader is judging on. Kept on
            its own line so a badge can never orphan itself when a country name
            makes the facts line longer. */}
        <span className="row__badges">
          <span className={`badge badge--${TONE_CLASS[fitTone(tender.fit_status)]}`}>
            {fitLabel(tender.fit_status)}
          </span>
          <span className={`badge badge--${TONE_CLASS[deploymentTone(tender.deployment_fit)]}`}>
            {deploymentLabel(tender.deployment_fit)}
          </span>
          {isNew ? <span className="badge badge--new">New</span> : null}
          {!tender.is_actionable ? <span className="badge badge--flat">Closed</span> : null}
        </span>

        <span className="row__meta">
          {meta.map((part, index) => (
            <span key={`${part}-${index}`}>
              {index > 0 ? <span className="sep">·&nbsp;</span> : null}
              {part}
            </span>
          ))}
        </span>

        {disqualifier ? (
          <span className="row__why row__why--bad">
            <Icon name="block" size={13} />
            {disqualifier}
          </span>
        ) : flag ? (
          <span className="row__why row__why--warn">
            <Icon name="warn" size={13} />
            {flag}
          </span>
        ) : reason ? (
          <span className="row__why">
            <Icon name="check" size={13} />
            {reason}
          </span>
        ) : null}
      </span>

      <span className="row__side">
        <span className="row__deadline">{tender.deadline ? formatDate(tender.deadline) : '—'}</span>
        <span
          className={`row__left${
            urgency === 'urgent'
              ? ' row__left--urgent'
              : urgency === 'soon'
                ? ' row__left--soon'
                : ''
          }`}
        >
          {label}
        </span>
        <span className="row__value">{formatValue(tender.estimated_value, tender.currency)}</span>
      </span>
    </button>
  );
}

function Skeletons() {
  return (
    <div className="rows" aria-busy="true">
      <span className="sr">Loading tenders…</span>
      {[68, 54, 74, 61, 47].map((width, index) => (
        <div className="skel" key={index}>
          <div>
            <div className="sk" style={{ height: 17, marginBottom: 5 }} />
            <div className="sk" style={{ height: 3 }} />
          </div>
          <div>
            <div className="sk" style={{ height: 15, width: `${width}%`, marginBottom: 8 }} />
            <div className="sk" style={{ height: 12, width: '38%' }} />
          </div>
          <div>
            <div className="sk" style={{ height: 12, width: '70%', marginLeft: 'auto' }} />
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
  selectedId,
  newSince,
  filterCount,
  total,
  onSelect,
  onRetry,
  onClearFilters,
  onFirstPage,
}: TenderListProps) {
  if (error) {
    return (
      <div className="state state--error" role="alert">
        <h3>Cannot reach the API</h3>
        <p>
          {error} Start it with <code>docker compose up -d</code>, then retry.
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
        <h3>{pastEnd ? 'Nothing on this page' : 'Nothing matches'}</h3>
        <p>
          {pastEnd
            ? `There ${total === 1 ? 'is 1 tender' : `are ${total.toLocaleString('en-GB')} tenders`} in this view, but none on this page.`
            : filterCount > 0
              ? `${filterCount} ${filterCount === 1 ? 'filter is' : 'filters are'} narrowing this down. Clearing them shows everything stored.`
              : 'Nothing has been stored yet. The next sweep is shown at the top of the page.'}
        </p>
        {pastEnd || filterCount > 0 ? (
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
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }

  const isNew = (tender: Tender): boolean =>
    Boolean(newSince) && new Date(tender.first_seen_at).getTime() >= new Date(newSince!).getTime();

  return (
    <div className="rows">
      {tenders.map((tender) => (
        <Row
          key={tender.id}
          tender={tender}
          selected={tender.id === selectedId}
          isNew={isNew(tender)}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}
