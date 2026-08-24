import { useCallback, useEffect, useState } from 'react';
import { ApiError, api } from '../api/client';
import type { TenderDetail } from '../types';
import {
  countryLabel,
  deadlineUrgency,
  deploymentLabel,
  deploymentTone,
  fitLabel,
  fitTone,
  formatDate,
  formatDateTime,
  formatValue,
  linkLabel,
  safeHref,
  scoreTone,
} from '../labels';
import { Icon } from './Icon';

/**
 * The one overlay that earns being an overlay: detail in context, deep-linked by
 * `?tender=`, and the target of every Slack digest link.
 *
 * Escape closes it, focus moves in on open and back to the row on close, Tab
 * cycles inside while it is up, and j/k step through the list without leaving.
 */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

const WEIGHTS = [
  { key: 'topic', label: 'Topic relevance', weight: 0.55 },
  { key: 'product', label: 'Product and hosting fit', weight: 0.3 },
  { key: 'intent', label: 'Procurement intent', weight: 0.15 },
] as const;

const TONE_CLASS = { green: 'good', amber: 'warn', red: 'bad', grey: 'flat' } as const;

export function DetailPanel({
  tenderId,
  position,
  onClose,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
}: {
  tenderId: number | null;
  position: { index: number; total: number } | null;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  hasPrev: boolean;
  hasNext: boolean;
}) {
  const [tender, setTender] = useState<TenderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const open = tenderId !== null;

  useEffect(() => {
    if (tenderId === null) {
      setTender(null);
      setError(null);
      return;
    }
    let cancelled = false;
    api
      .tender(tenderId)
      .then((data) => !cancelled && (setTender(data), setError(null)))
      .catch((err) => {
        if (cancelled) return;
        setTender(null);
        setError(err instanceof ApiError ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  const flash = useCallback((what: string) => {
    setCopied(what);
    window.setTimeout(() => setCopied(null), 1600);
  }, []);

  // Keyboard: Escape closes, Tab is trapped, j/k walk the list.
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const panel = document.getElementById('detail-panel');
    panel?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const handle = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      const target = event.target as HTMLElement | null;
      const typing = target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
      if (event.key === 'Tab' && panel) {
        const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
          (el) => el.offsetParent !== null,
        );
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        }
        return;
      }
      if (typing) return;
      if (event.key === 'j' && hasNext) {
        event.preventDefault();
        onNext();
      } else if (event.key === 'k' && hasPrev) {
        event.preventDefault();
        onPrev();
      }
    };
    document.addEventListener('keydown', handle);
    return () => {
      document.removeEventListener('keydown', handle);
      document.body.style.overflow = previousOverflow;
      opener?.focus?.();
    };
  }, [open, onClose, onNext, onPrev, hasNext, hasPrev]);

  const sub = tender
    ? {
        topic: tender.topic_relevance_score,
        product: tender.product_fit_score,
        intent: tender.procurement_intent_score,
      }
    : { topic: 0, product: 0, intent: 0 };
  const weighted = 0.55 * sub.topic + 0.3 * sub.product + 0.15 * sub.intent;
  const rounded = Math.round(weighted);
  const capped = tender ? rounded !== tender.relevance_score : false;
  const noticeHref = safeHref(tender?.source_url);
  const urgency = tender ? deadlineUrgency(tender.deadline) : null;

  return (
    <aside
      id="detail-panel"
      className={`panel${open ? ' is-on' : ''}`}
      role="dialog"
      aria-modal={open}
      aria-label="Tender detail"
      aria-hidden={!open}
      style={open ? undefined : { visibility: 'hidden' }}
    >
      <header className="panel__head">
        {tender ? (
          <span
            className={`score score--${TONE_CLASS[scoreTone(tender.relevance_score)]}`}
            style={{ flex: 'none', width: 40 }}
          >
            <span className="score__n">{tender.relevance_score}</span>
            <span className="score__bar">
              <i style={{ width: `${Math.max(4, tender.relevance_score)}%` }} />
            </span>
          </span>
        ) : null}
        <div style={{ minWidth: 0 }}>
          <h2>{tender?.title ?? (error ? 'Could not load this tender' : 'Loading…')}</h2>
          {tender ? (
            <p>
              {tender.buyer_name ?? 'Buyer not published'} · {tender.source} ·{' '}
              <span className="mono">{tender.reference_number ?? tender.source_notice_id}</span>
            </p>
          ) : null}
        </div>
        <div className="panel__nav">
          <button
            type="button"
            className="btn btn--icon"
            onClick={onPrev}
            disabled={!hasPrev}
            aria-label="Previous tender"
            title="Previous (k)"
          >
            <Icon name="prev" size={15} />
          </button>
          <button
            type="button"
            className="btn btn--icon"
            onClick={onNext}
            disabled={!hasNext}
            aria-label="Next tender"
            title="Next (j)"
          >
            <Icon name="next" size={15} />
          </button>
          <button type="button" className="btn btn--icon" onClick={onClose} aria-label="Close">
            <Icon name="close" size={16} />
          </button>
        </div>
      </header>

      <div className="panel__body">
        {error ? (
          <div className="state state--error" role="alert">
            <h3>Could not load this tender</h3>
            <p>{error}</p>
          </div>
        ) : null}

        {tender ? (
          <>
            <div className="badges">
              <span className={`badge badge--${TONE_CLASS[fitTone(tender.fit_status)]}`}>
                {fitLabel(tender.fit_status)}
              </span>
              <span className={`badge badge--${TONE_CLASS[deploymentTone(tender.deployment_fit)]}`}>
                {deploymentLabel(tender.deployment_fit)}
              </span>
              {tender.relevance_category ? (
                <span className="badge badge--flat">
                  {tender.relevance_category.replace(/_/g, ' ')}
                </span>
              ) : null}
              <span className="badge badge--flat">Found {formatDate(tender.first_seen_at)}</span>
            </div>

            <section className="sec">
              <h3>Why this scores {tender.relevance_score}</h3>
              <p className="formula">
                <b>0.55</b> × topic <b>{sub.topic}</b> &nbsp;+&nbsp; <b>0.30</b> × product and
                hosting <b>{sub.product}</b> &nbsp;+&nbsp; <b>0.15</b> × intent <b>{sub.intent}</b>
                &nbsp;=&nbsp; <b>{weighted.toFixed(2)}</b> &nbsp;→&nbsp; <b>{rounded}</b>
                {capped ? (
                  <>
                    , then capped to <b>{tender.relevance_score}</b>
                    {tender.disqualifiers.length ? ' by the disqualifier below' : ''}
                    {!tender.is_actionable ? ' (no longer open)' : ''}.
                  </>
                ) : (
                  '. Nothing capped it.'
                )}
              </p>

              <div className="meters">
                {WEIGHTS.map((row) => (
                  <div className="meter" key={row.key}>
                    <div className="meter__top">
                      <span>
                        {row.label} <span className="meter__w">× {row.weight.toFixed(2)}</span>
                      </span>
                      <b>{sub[row.key]}</b>
                    </div>
                    <div
                      className="meter__track"
                      role="meter"
                      aria-valuenow={sub[row.key]}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label={row.label}
                    >
                      <div
                        className="meter__fill"
                        style={{ width: `${Math.max(0, Math.min(100, sub[row.key]))}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>

              {tender.relevance_reasons.length ? (
                <ul className="reasons">
                  {tender.relevance_reasons.map((reason) => (
                    <li key={reason}>
                      <Icon name="check" size={13} />
                      {reason}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">No positive signals recorded.</p>
              )}

              {tender.disqualifiers.length ? (
                <>
                  <h4 className="subhead">Ruled out because</h4>
                  <ul className="reasons reasons--bad">
                    {tender.disqualifiers.map((item) => (
                      <li key={item}>
                        <Icon name="block" size={13} />
                        {item}
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}

              {tender.review_flags.length ? (
                <>
                  <h4 className="subhead">Worth checking by hand</h4>
                  <ul className="reasons reasons--warn">
                    {tender.review_flags.map((item) => (
                      <li key={item}>
                        <Icon name="warn" size={13} />
                        {item}
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
            </section>

            <section className="sec">
              <h3>Key facts</h3>
              <dl className="facts">
                <div>
                  <dt>Buyer</dt>
                  <dd>{tender.buyer_name ?? '—'}</dd>
                </div>
                <div>
                  <dt>Country</dt>
                  <dd>{countryLabel(tender.buyer_country)}</dd>
                </div>
                <div>
                  <dt>Published</dt>
                  <dd className="num">{formatDate(tender.publication_date)}</dd>
                </div>
                <div>
                  <dt>Deadline</dt>
                  <dd className="num">
                    {formatDateTime(tender.deadline)}
                    {urgency && urgency.urgency !== 'none' ? (
                      <span className="muted"> · {urgency.label}</span>
                    ) : null}
                  </dd>
                </div>
                <div>
                  <dt>Estimated value</dt>
                  <dd className="num">{formatValue(tender.estimated_value, tender.currency)}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd>
                    {tender.status ?? '—'}
                    {tender.procurement_stage ? ` · ${tender.procurement_stage}` : ''}
                  </dd>
                </div>
                <div>
                  <dt>Delivery location</dt>
                  <dd>{tender.delivery_location ?? '—'}</dd>
                </div>
                <div>
                  <dt>Source</dt>
                  <dd className="mono">{tender.source}</dd>
                </div>
              </dl>
            </section>

            {tender.classification_codes.length ? (
              <section className="sec">
                <h3>Classification</h3>
                <div className="tags">
                  {tender.classification_codes.map((code, index) => (
                    <span className="tag" key={`${code.code}-${index}`}>
                      {[code.scheme, code.code].filter(Boolean).join(' ')}
                      {code.description ? ` — ${code.description}` : ''}
                    </span>
                  ))}
                </div>
              </section>
            ) : null}

            {tender.description ? (
              <section className="sec">
                <h3>Description</h3>
                <p className="prose">{tender.description}</p>
              </section>
            ) : null}

            <section className="sec">
              <h3>Links</h3>
              <div className="links">
                {noticeHref ? (
                  <a href={noticeHref} target="_blank" rel="noreferrer noopener">
                    Original notice
                    <Icon name="external" size={12} />
                  </a>
                ) : (
                  <p className="muted">
                    {tender.source_url
                      ? 'This feed published a link that is not a usable web address.'
                      : 'This feed published no notice link.'}
                  </p>
                )}
                {tender.document_urls.map((url) => {
                  const href = safeHref(url);
                  return href ? (
                    <a key={url} href={href} target="_blank" rel="noreferrer noopener">
                      {linkLabel(href)}
                      <Icon name="external" size={12} />
                    </a>
                  ) : null;
                })}
              </div>
            </section>

            {tender.raw_payload ? (
              <section className="sec">
                <details className="raw">
                  <summary>
                    <Icon name="chevronRight" size={12} />
                    Raw source data
                  </summary>
                  <div className="raw__actions">
                    <button
                      type="button"
                      className="btn btn--quiet"
                      onClick={() =>
                        void navigator.clipboard
                          ?.writeText(JSON.stringify(tender.raw_payload, null, 2))
                          .then(() => flash('json'))
                      }
                    >
                      <Icon name="copy" size={12} />
                      {copied === 'json' ? 'Copied' : 'Copy JSON'}
                    </button>
                  </div>
                  <pre>{JSON.stringify(tender.raw_payload, null, 2)}</pre>
                </details>
              </section>
            ) : null}
          </>
        ) : null}
      </div>

      <footer className="panel__foot">
        <span className="grow num">
          {position ? (
            <>
              {position.index} of {position.total} · <span className="kbd">j</span>{' '}
              <span className="kbd">k</span> to move
            </>
          ) : null}
        </span>
        <button
          type="button"
          className="btn btn--quiet"
          onClick={() =>
            void navigator.clipboard?.writeText(window.location.href).then(() => flash('link'))
          }
        >
          <Icon name="link" size={13} />
          {copied === 'link' ? 'Copied' : 'Copy link'}
        </button>
        {noticeHref ? (
          <a
            className="btn btn--primary"
            href={noticeHref}
            target="_blank"
            rel="noreferrer noopener"
          >
            Open notice
            <Icon name="external" size={13} />
          </a>
        ) : null}
      </footer>
    </aside>
  );
}
