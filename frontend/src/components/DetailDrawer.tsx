import { useCallback, useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { TenderDetail } from '../types';
import {
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
import { Drawer } from './Drawer';
import { Icon } from './Icon';

/**
 * The detail panel explains the score visually (delta 8).
 *
 * Three weighted subscore meters and the formula that combines them, reasons /
 * disqualifiers / review flags grouped rather than merged into one list,
 * previous-next navigation (j and k), and a copy button on the raw payload.
 *
 * The weights are the engine's own: final = 0.55*topic + 0.30*product_fit
 * + 0.15*procurement_intent, then caps and non-actionable multipliers.
 */
const WEIGHTS = [
  { key: 'topic', label: 'Topic relevance', weight: 0.55, meter: 'meter--w1' },
  { key: 'product', label: 'Product & deployment', weight: 0.3, meter: 'meter--w2' },
  { key: 'intent', label: 'Procurement intent', weight: 0.15, meter: 'meter--w3' },
] as const;

export interface DetailDrawerProps {
  tenderId: number | null;
  position: { index: number; total: number } | null;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  hasPrev: boolean;
  hasNext: boolean;
}

export function DetailDrawer({
  tenderId,
  position,
  onClose,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
}: DetailDrawerProps) {
  const [tender, setTender] = useState<TenderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (tenderId === null) {
      setTender(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .tender(tenderId)
      .then((data) => {
        if (!cancelled) {
          setTender(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setTender(null);
          setError(err instanceof ApiError ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  // j / k move through the result list without leaving the panel.
  const onKeyDown = useCallback(
    (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (event.key === 'j' && hasNext) {
        event.preventDefault();
        onNext();
      } else if (event.key === 'k' && hasPrev) {
        event.preventDefault();
        onPrev();
      }
    },
    [hasNext, hasPrev, onNext, onPrev],
  );

  const copyPayload = async () => {
    if (!tender?.raw_payload) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(tender.raw_payload, null, 2));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard blocked: the URL bar already shows the shareable link */
    }
  };

  const open = tenderId !== null;
  const subscores = tender
    ? {
        topic: tender.topic_relevance_score,
        product: tender.product_fit_score,
        intent: tender.procurement_intent_score,
      }
    : { topic: 0, product: 0, intent: 0 };
  // The engine computes int(round(weighted)) and *then* applies caps and the
  // non-actionable multipliers, so the rounded weighted sum is the honest
  // "before" figure. Shown to two decimals: 97.45 -> 97 reads correctly, where
  // one decimal (97.5 -> 97) looks like an off-by-one.
  const weighted = tender
    ? 0.55 * subscores.topic + 0.3 * subscores.product + 0.15 * subscores.intent
    : 0;
  const rounded = Math.round(weighted);
  const capped = tender ? rounded !== tender.relevance_score : false;
  const urgency = tender ? deadlineUrgency(tender.deadline) : null;
  // Feed-supplied; must be scheme-checked before it becomes an href.
  const noticeHref = safeHref(tender?.source_url);

  return (
    <Drawer
      open={open}
      onClose={onClose}
      label="Tender details"
      className="drawer--detail"
      onKeyDown={onKeyDown}
    >
      <header className="drawer__head">
        {tender ? (
          <span
            className={`score score--${scoreTone(tender.relevance_score)} num`}
            style={{ flex: 'none' }}
          >
            {tender.relevance_score}
          </span>
        ) : null}
        <div style={{ minWidth: 0 }}>
          <h2 className="detail__title">
            {loading ? 'Loading…' : (tender?.title ?? (error ? 'Could not load this tender' : ''))}
          </h2>
          {tender ? (
            <p>
              {tender.source} · {tender.reference_number ?? tender.source_notice_id} ·{' '}
              {tender.buyer_name ?? 'buyer not published'}
            </p>
          ) : null}
        </div>
        <span className="spacer" />
        <div className="detail__nav">
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
          <button
            type="button"
            className="btn btn--icon"
            onClick={onClose}
            aria-label="Close details"
          >
            <Icon name="close" size={16} />
          </button>
        </div>
      </header>

      <div className="drawer__body">
        {error ? (
          <div className="state state--error" role="alert" style={{ marginTop: 16 }}>
            <div className="state__icon">
              <Icon name="cross" size={22} />
            </div>
            <h3>Could not load this tender</h3>
            <p>{error}</p>
          </div>
        ) : null}

        {tender ? (
          <>
            <div className="badges" style={{ margin: '14px 0 0' }}>
              <span className={`badge badge--${fitTone(tender.fit_status)}`}>
                <Icon
                  name={fitTone(tender.fit_status) === 'green' ? 'check' : 'warning'}
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
              <span className="badge badge--new">
                First seen {formatDate(tender.first_seen_at)}
              </span>
            </div>

            <section className="dsection">
              <h3>
                Why this score <span className="muted">{tender.relevance_score} / 100</span>
              </h3>
              <div className="formula">
                <b>0.55</b> × topic relevance <b className="num">{subscores.topic}</b> &nbsp;+&nbsp;{' '}
                <b>0.30</b> × product &amp; deployment <b className="num">{subscores.product}</b>{' '}
                &nbsp;+&nbsp; <b>0.15</b> × procurement intent{' '}
                <b className="num">{subscores.intent}</b> &nbsp;=&nbsp;{' '}
                <b className="num">{weighted.toFixed(2)}</b> &nbsp;→&nbsp;{' '}
                <b className="num">{rounded}</b>
                {capped ? (
                  <>
                    , then capped or scaled to <b className="num">{tender.relevance_score}</b>
                    {tender.disqualifiers.length ? ' by the disqualifiers below' : ''}
                    {!tender.is_actionable ? ' (not actionable)' : ''}.
                  </>
                ) : (
                  <>. No caps or multipliers applied.</>
                )}
              </div>

              <div className="meters">
                {WEIGHTS.map((row) => {
                  const value = subscores[row.key];
                  return (
                    <div className={`meter ${row.meter}`} key={row.key}>
                      <div className="meter__top">
                        <span>
                          {row.label}{' '}
                          <span className="meter__weight">weight {row.weight.toFixed(2)}</span>
                        </span>
                        <b className="num">{value}</b>
                      </div>
                      <div
                        className="meter__track"
                        role="meter"
                        aria-valuenow={value}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-label={`${row.label} subscore`}
                      >
                        <div
                          className="meter__fill"
                          style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>

              {tender.relevance_reasons.length ? (
                <ul className="reasons reasons--good">
                  {tender.relevance_reasons.map((reason) => (
                    <li key={reason}>
                      <Icon name="check" size={14} />
                      {reason}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">No positive signals recorded.</p>
              )}

              <h4 className="subhead subhead--bad">
                <Icon name="cross" size={13} />
                Disqualifiers {tender.disqualifiers.length ? '' : '— none'}
              </h4>
              {tender.disqualifiers.length ? (
                <ul className="reasons reasons--bad">
                  {tender.disqualifiers.map((item) => (
                    <li key={item}>
                      <Icon name="cross" size={14} />
                      {item}
                    </li>
                  ))}
                </ul>
              ) : null}

              <h4 className="subhead subhead--flag">
                <Icon name="warning" size={13} />
                Manual-review flags {tender.review_flags.length ? '' : '— none'}
              </h4>
              {tender.review_flags.length ? (
                <ul className="reasons reasons--flag">
                  {tender.review_flags.map((item) => (
                    <li key={item}>
                      <Icon name="warning" size={14} />
                      {item}
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>

            <section className="dsection">
              <h3>Key facts</h3>
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
                  <dd>
                    {formatDateTime(tender.deadline)}
                    {urgency && urgency.urgency !== 'none' ? (
                      <span className="muted"> ({urgency.label})</span>
                    ) : null}
                  </dd>
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
                  <dt>First / last seen</dt>
                  <dd>
                    {formatDate(tender.first_seen_at)} · {formatDate(tender.last_seen_at)}
                  </dd>
                </div>
                <div>
                  <dt>Source timezone</dt>
                  <dd>{tender.source_timezone ?? '—'}</dd>
                </div>
              </dl>
            </section>

            {tender.classification_codes.length ? (
              <section className="dsection">
                <h3>Classification codes</h3>
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
              <section className="dsection">
                <h3>Description</h3>
                <p className="desc">{tender.description}</p>
              </section>
            ) : null}

            <section className="dsection">
              <h3>Links &amp; documents</h3>
              <div className="linklist">
                {noticeHref ? (
                  <a href={noticeHref} target="_blank" rel="noreferrer noopener">
                    Original notice on {tender.source}
                    <Icon name="external" size={12} />
                  </a>
                ) : (
                  <p className="muted">
                    {tender.source_url
                      ? 'This feed published a notice link that is not a usable web address.'
                      : 'This feed published no notice URL.'}
                  </p>
                )}
                {tender.document_urls.map((url) => {
                  const href = safeHref(url);
                  return href ? (
                    <a key={url} href={href} target="_blank" rel="noreferrer noopener">
                      {linkLabel(href)}
                      <Icon name="external" size={12} />
                    </a>
                  ) : (
                    <span key={url} className="muted mono">
                      {url}
                    </span>
                  );
                })}
              </div>
            </section>

            {tender.raw_payload ? (
              <section className="dsection">
                <details className="raw">
                  <summary>
                    Raw source metadata
                    <button
                      type="button"
                      className="btn btn--sm copybtn"
                      onClick={(event) => {
                        event.preventDefault();
                        void copyPayload();
                      }}
                    >
                      <Icon name="copy" size={12} />
                      {copied ? 'Copied' : 'Copy JSON'}
                    </button>
                  </summary>
                  <div className="raw__body">
                    <pre>{JSON.stringify(tender.raw_payload, null, 2)}</pre>
                  </div>
                </details>
              </section>
            ) : null}
          </>
        ) : null}
      </div>

      <footer className="drawer__foot">
        <span className="count">
          {position ? (
            <>
              Tender <b className="num">{position.index}</b> of{' '}
              <b className="num">{position.total}</b>
              <span className="muted">
                {' '}
                · <span className="kbd">j</span> <span className="kbd">k</span> to move
              </span>
            </>
          ) : null}
        </span>
        <button type="button" className="btn" onClick={() => void copyLink()}>
          <Icon name="link" size={12} />
          Copy link
        </button>
        {noticeHref ? (
          <a
            className="btn btn--primary"
            href={noticeHref}
            target="_blank"
            rel="noreferrer noopener"
          >
            Open original notice
            <Icon name="external" size={12} />
          </a>
        ) : null}
      </footer>
    </Drawer>
  );
}
