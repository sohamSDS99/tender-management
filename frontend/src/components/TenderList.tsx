import type { Tender } from '../types';
import {
  deadlineUrgency,
  deploymentLabel,
  deploymentTone,
  fitLabel,
  fitTone,
  formatDate,
  formatValue,
  scoreTone,
} from '../labels';
import { Icon } from './Icon';

/**
 * Result cards (delta 6) and the real loading / empty / error states (delta 9).
 *
 * Each card carries the estimated value, a deadline colour-coded at 14 days and
 * 72 hours, a title clamped to two lines, a "New" marker for anything first seen
 * in the last run, and the top relevance reason prefixed with an icon.
 */
export interface TenderListProps {
  tenders: Tender[];
  loading: boolean;
  error: string | null;
  selectedId: number | null;
  /** Start of the most recent run; anything first seen at or after it is new. */
  newSince: string | null;
  activeFilterCount: number;
  onSelect: (id: number) => void;
  onRetry: () => void;
  onClearFilters: () => void;
  onLowerScore: () => void;
}

function isNew(tender: Tender, newSince: string | null): boolean {
  if (!newSince) return false;
  return new Date(tender.first_seen_at).getTime() >= new Date(newSince).getTime();
}

function Skeletons() {
  return (
    <div className="list" aria-busy="true" aria-live="polite">
      <span className="sr">Loading tenders…</span>
      {[72, 58, 66, 80].map((width, index) => (
        <div className="skel" key={index}>
          <div className="sk sk--pill" />
          <div>
            <div className="sk sk--h" style={{ width: `${width}%` }} />
            <div className="sk sk--s" style={{ width: '42%', marginBottom: 8 }} />
            <div className="sk sk--s" style={{ width: '88%' }} />
          </div>
          <div>
            <div className="sk sk--s" style={{ width: '100%', marginBottom: 8 }} />
            <div className="sk sk--s" style={{ width: '60%', marginLeft: 'auto' }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function ResultCard({
  tender,
  selected,
  isNewTender,
  onSelect,
}: {
  tender: Tender;
  selected: boolean;
  isNewTender: boolean;
  onSelect: (id: number) => void;
}) {
  const { urgency, label } = deadlineUrgency(tender.deadline);
  const reason = tender.relevance_reasons[0];
  const disqualifier = tender.disqualifiers[0];
  const flag = tender.review_flags[0];

  return (
    <button
      type="button"
      className={`rescard${selected ? ' is-selected' : ''}`}
      onClick={() => onSelect(tender.id)}
      aria-label={`${tender.title}. Score ${tender.relevance_score}. ${fitLabel(tender.fit_status)}.`}
    >
      <div>
        <span className={`score score--${scoreTone(tender.relevance_score)} num`}>
          {tender.relevance_score}
        </span>
      </div>

      <div>
        <h3 className="rescard__title">{tender.title || '(untitled notice)'}</h3>
        <div className="badges">
          <span className={`badge badge--${fitTone(tender.fit_status)}`}>
            <Icon
              name={
                fitTone(tender.fit_status) === 'green'
                  ? 'check'
                  : fitTone(tender.fit_status) === 'amber'
                    ? 'warning'
                    : 'cross'
              }
              size={11}
            />
            {fitLabel(tender.fit_status)}
          </span>
          <span className={`badge badge--${deploymentTone(tender.deployment_fit)}`}>
            {deploymentLabel(tender.deployment_fit)}
          </span>
          {tender.relevance_category ? (
            <span className="badge badge--line">
              {tender.relevance_category.replace(/_/g, ' ')}
            </span>
          ) : null}
          {!tender.is_actionable ? <span className="badge badge--grey">Not actionable</span> : null}
          {isNewTender ? <span className="badge badge--new">New</span> : null}
        </div>

        <p className="metaline">
          {tender.buyer_name ? <span>{tender.buyer_name}</span> : null}
          {tender.buyer_country ? <span>{tender.buyer_country}</span> : null}
          <span className="mono">{tender.source}</span>
          {tender.publication_date ? (
            <span>published {formatDate(tender.publication_date)}</span>
          ) : null}
          {tender.procurement_stage ? <span>{tender.procurement_stage}</span> : null}
        </p>

        {reason ? (
          <p className="rescard__why">
            <Icon name="check" size={13} />
            {reason}
          </p>
        ) : null}
        {disqualifier ? (
          <p className="flagline flagline--bad">
            <Icon name="cross" size={13} />
            {disqualifier}
          </p>
        ) : null}
        {!disqualifier && flag ? (
          <p className="flagline flagline--flag">
            <Icon name="warning" size={13} />
            {flag}
          </p>
        ) : null}
      </div>

      <div className="rescard__side">
        <span className={`deadline${urgency === 'none' ? '' : ` deadline--${urgency}`}`}>
          <b>{tender.deadline ? formatDate(tender.deadline) : '—'}</b>
          <em>
            {urgency === 'urgent' || urgency === 'soon' || urgency === 'normal' ? (
              <Icon name="clock" size={11} />
            ) : null}
            {label}
          </em>
        </span>
        <span className={`value${tender.estimated_value === null ? ' muted' : ''}`}>
          {formatValue(tender.estimated_value, tender.currency)}
        </span>
        {tender.source_url ? (
          <span className="openlink">
            Original notice
            <Icon name="external" size={11} />
          </span>
        ) : null}
      </div>
    </button>
  );
}

export function TenderList({
  tenders,
  loading,
  error,
  selectedId,
  newSince,
  activeFilterCount,
  onSelect,
  onRetry,
  onClearFilters,
  onLowerScore,
}: TenderListProps) {
  if (error) {
    return (
      <div className="state state--error" role="alert">
        <div className="state__icon">
          <Icon name="cross" size={22} />
        </div>
        <h3>Cannot reach the API</h3>
        <p>
          {error} Start the backend with <code>docker compose up -d</code>, or{' '}
          <code>uvicorn app.main:app --port 8000</code> from <code>backend/</code>. In development
          the Vite proxy expects port 8000.
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
    return (
      <div className="state state--empty">
        <div className="state__icon">
          <Icon name="search" size={22} />
        </div>
        <h3>No tenders match these filters</h3>
        <p>
          {activeFilterCount > 0
            ? `${activeFilterCount} ${activeFilterCount === 1 ? 'filter is' : 'filters are'} narrowing the results. Try lowering the minimum score or widening the deadline window.`
            : 'Nothing is stored yet. The next automated run is shown in the header — or run the sweep manually from the runbook.'}
        </p>
        {activeFilterCount > 0 ? (
          <div className="state__actions">
            <button type="button" className="btn btn--primary" onClick={onClearFilters}>
              Clear all filters
            </button>
            <button type="button" className="btn" onClick={onLowerScore}>
              Lower minimum score to 25
            </button>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="list">
      {tenders.map((tender) => (
        <ResultCard
          key={tender.id}
          tender={tender}
          selected={tender.id === selectedId}
          isNewTender={isNew(tender, newSince)}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}
