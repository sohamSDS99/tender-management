import type { Tender } from '../types';
import { CATEGORY_LABELS, daysUntil, formatDate } from '../labels';
import { DeploymentBadge, FitBadge, ScorePill } from './Badges';

interface Props {
  tenders: Tender[];
  loading: boolean;
  error: string | null;
  selectedId: number | null;
  onSelect: (id: number) => void;
}

function DeadlineCell({ tender }: { tender: Tender }) {
  const days = daysUntil(tender.deadline);
  if (!tender.deadline) return <>—</>;
  const soon = days !== null && days >= 0 && days <= 14;
  const gone = days !== null && days < 0;
  return (
    <span
      className={soon ? 'deadline deadline--soon' : gone ? 'deadline deadline--gone' : 'deadline'}
    >
      {formatDate(tender.deadline)}
      {days !== null && <em>{gone ? 'closed' : days === 0 ? 'today' : `${days}d left`}</em>}
    </span>
  );
}

export function TenderList({ tenders, loading, error, selectedId, onSelect }: Props) {
  if (error) {
    return (
      <div className="state state--error" role="alert">
        <strong>Could not load tenders.</strong>
        <p>{error}</p>
      </div>
    );
  }
  if (loading && tenders.length === 0) {
    return (
      <div className="state">
        <span className="spinner" aria-hidden />
        Loading tenders…
      </div>
    );
  }
  if (tenders.length === 0) {
    return (
      <div className="state state--empty">
        <strong>No tenders match these filters.</strong>
        <p>
          Lower the minimum score, clear the filters, or run “Fetch new tenders” to pull the latest
          notices from the enabled sources.
        </p>
      </div>
    );
  }

  return (
    <div className={`list ${loading ? 'is-refreshing' : ''}`}>
      {tenders.map((tender) => (
        <article
          key={tender.id}
          className={`card ${selectedId === tender.id ? 'is-selected' : ''}`}
          onClick={() => onSelect(tender.id)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              onSelect(tender.id);
            }
          }}
          tabIndex={0}
          role="button"
          aria-label={`Open details for ${tender.title}`}
        >
          <div className="card__score">
            <ScorePill score={tender.relevance_score} />
          </div>
          <div className="card__body">
            <h3>{tender.title}</h3>
            <div className="card__badges">
              <FitBadge fit={tender.fit_status} />
              <DeploymentBadge deployment={tender.deployment_fit} />
              {tender.relevance_category && (
                <span className="badge badge--outline">
                  {CATEGORY_LABELS[tender.relevance_category] ?? tender.relevance_category}
                </span>
              )}
              {!tender.is_actionable && <span className="badge badge--grey">Not actionable</span>}
            </div>
            <p className="card__meta">
              <span>{tender.buyer_name ?? 'Unknown buyer'}</span>
              <span>{tender.buyer_country ?? '—'}</span>
              <span className="mono">{tender.source}</span>
              <span>published {formatDate(tender.publication_date)}</span>
              <span>{tender.status ?? '—'}</span>
            </p>
            {tender.relevance_reasons[0] && (
              <p className="card__why" title={tender.relevance_reasons.join('\n')}>
                {tender.relevance_reasons[0]}
              </p>
            )}
            {tender.disqualifiers[0] && (
              <p className="card__why card__why--bad">⛔ {tender.disqualifiers[0]}</p>
            )}
            {tender.review_flags[0] && (
              <p className="card__why card__why--flag">⚑ {tender.review_flags[0]}</p>
            )}
          </div>
          <div className="card__side">
            <DeadlineCell tender={tender} />
            {tender.source_url && (
              <a
                href={tender.source_url}
                target="_blank"
                rel="noreferrer"
                onClick={(event) => event.stopPropagation()}
              >
                Original notice ↗
              </a>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}
