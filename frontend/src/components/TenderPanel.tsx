import { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { TenderDetail } from '../types';
import { CATEGORY_LABELS, formatDate, formatDateTime, formatValue } from '../labels';
import { DeploymentBadge, FitBadge, ScorePill } from './Badges';

interface Props {
  tenderId: number | null;
  onClose: () => void;
}

export function TenderPanel({ tenderId, onClose }: Props) {
  const [tender, setTender] = useState<TenderDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (tenderId === null) {
      setTender(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .tender(tenderId)
      .then((data) => !cancelled && setTender(data))
      .catch((err: ApiError) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (tenderId === null) return null;

  return (
    <>
      <div className="overlay" onClick={onClose} />
      <aside className="panel" role="dialog" aria-modal="true" aria-label="Tender details">
        <header className="panel__head">
          <button className="panel__close" onClick={onClose} aria-label="Close details">
            ✕
          </button>
          {tender && <ScorePill score={tender.relevance_score} />}
          <h2>{loading && !tender ? 'Loading…' : (tender?.title ?? 'Tender')}</h2>
        </header>

        {error && (
          <div className="state state--error" role="alert">
            {error}
          </div>
        )}

        {tender && (
          <div className="panel__body">
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

            <dl className="facts">
              <div>
                <dt>Buyer</dt>
                <dd>{tender.buyer_name ?? '—'}</dd>
              </div>
              <div>
                <dt>Country</dt>
                <dd>{tender.buyer_country ?? '—'}</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd className="mono">{tender.source}</dd>
              </div>
              <div>
                <dt>Reference</dt>
                <dd className="mono">{tender.reference_number ?? tender.source_notice_id}</dd>
              </div>
              <div>
                <dt>Published</dt>
                <dd>{formatDate(tender.publication_date)}</dd>
              </div>
              <div>
                <dt>Deadline</dt>
                <dd>{formatDateTime(tender.deadline)}</dd>
              </div>
              <div>
                <dt>Estimated value</dt>
                <dd>{formatValue(tender.estimated_value, tender.currency)}</dd>
              </div>
              <div>
                <dt>Status / stage</dt>
                <dd>
                  {tender.status ?? '—'} · {tender.procurement_stage ?? '—'}
                </dd>
              </div>
              <div>
                <dt>Delivery location</dt>
                <dd>{tender.delivery_location ?? '—'}</dd>
              </div>
              <div>
                <dt>Notice type</dt>
                <dd>{tender.notice_type ?? '—'}</dd>
              </div>
              <div>
                <dt>First seen / last seen</dt>
                <dd>
                  {formatDate(tender.first_seen_at)} · {formatDate(tender.last_seen_at)}
                </dd>
              </div>
              <div>
                <dt>Source timezone</dt>
                <dd>{tender.source_timezone ?? 'unknown (stored as UTC)'}</dd>
              </div>
            </dl>

            <section className="panel__section">
              <h3>Why this score ({tender.relevance_score}/100)</h3>
              <p className="subscores">
                topic {tender.topic_relevance_score} · product &amp; deployment fit{' '}
                {tender.product_fit_score} · procurement intent {tender.procurement_intent_score}
              </p>
              <ul className="reasons">
                {tender.relevance_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
              {tender.disqualifiers.length > 0 && (
                <>
                  <h4 className="bad">Disqualifiers</h4>
                  <ul className="reasons reasons--bad">
                    {tender.disqualifiers.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
              {tender.review_flags.length > 0 && (
                <>
                  <h4 className="flag">Manual-review flags</h4>
                  <ul className="reasons reasons--flag">
                    {tender.review_flags.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
            </section>

            {tender.classification_codes.length > 0 && (
              <section className="panel__section">
                <h3>Classification codes</h3>
                <div className="codes">
                  {tender.classification_codes.map((code, index) => (
                    <span className="tag" key={`${code.scheme}-${code.code}-${index}`}>
                      {code.scheme}: {code.code}
                      {code.description ? ` — ${code.description}` : ''}
                    </span>
                  ))}
                </div>
              </section>
            )}

            <section className="panel__section">
              <h3>Description</h3>
              <p className="description">{tender.description ?? 'No description published.'}</p>
            </section>

            <section className="panel__section">
              <h3>Links</h3>
              <ul className="links">
                {tender.source_url && (
                  <li>
                    <a href={tender.source_url} target="_blank" rel="noreferrer">
                      Original notice ↗
                    </a>
                  </li>
                )}
                {tender.document_urls.map((url) => (
                  <li key={url}>
                    <a href={url} target="_blank" rel="noreferrer">
                      {url.length > 80 ? `${url.slice(0, 80)}…` : url}
                    </a>
                  </li>
                ))}
                {!tender.source_url && tender.document_urls.length === 0 && (
                  <li className="muted">No links published.</li>
                )}
              </ul>
            </section>

            <details className="panel__section raw">
              <summary>Raw source metadata</summary>
              <pre>{JSON.stringify(tender.raw_payload, null, 2)}</pre>
            </details>
          </div>
        )}
      </aside>
    </>
  );
}
