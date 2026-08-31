import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api } from '../api/client';
import type { FeedbackResponse, TenderDetail, Translation, Verdict } from '../types';
import type { ScoreBands } from '../labels';
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
  languageLabel,
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

/** The tone names the stylesheet uses, keyed by the tone the labels module returns. */
const TONE_CLASS = { green: 'green', amber: 'amber', red: 'red', grey: 'grey' } as const;

export function DetailPanel({
  bands,
  sourceLabel,
  tenderId,
  position,
  onClose,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
  onFeedback,
}: {
  bands: ScoreBands;
  sourceLabel: (key: string) => string;
  tenderId: number | null;
  position: { index: number; total: number } | null;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  hasPrev: boolean;
  hasNext: boolean;
  /** Told what the mark changed, so the page behind can say so and reload. */
  onFeedback: (result: FeedbackResponse) => void;
}) {
  const [tender, setTender] = useState<TenderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [marking, setMarking] = useState(false);
  // Kept separate from `error`: a failed mark must not replace the notice the
  // reader is looking at with an error page.
  const [markError, setMarkError] = useState<string | null>(null);
  // Translation state, all three reset per notice below. j/k steps through the
  // list without closing the panel, so state left behind would show one
  // notice's English under the next notice's Portuguese.
  const [translation, setTranslation] = useState<Translation | null>(null);
  const [translating, setTranslating] = useState(false);
  const [translateError, setTranslateError] = useState<string | null>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const open = tenderId !== null;

  // Which notice is on screen right now, readable from inside an awaited
  // callback. `tenderId` closed over in `translate` is the notice the request
  // was *for*; this is the one the answer would land on.
  const shownId = useRef<number | null>(tenderId);
  shownId.current = tenderId;

  const resetTranslation = useCallback(() => {
    setTranslation(null);
    setTranslating(false);
    setTranslateError(null);
    setShowOriginal(false);
  }, []);

  useEffect(() => {
    if (tenderId === null) {
      setTender(null);
      setError(null);
      resetTranslation();
      return;
    }
    let cancelled = false;
    setMarkError(null);
    resetTranslation();
    api
      .tender(tenderId)
      .then(
        (data) =>
          !cancelled &&
          // The note field is seeded from the stored note, so editing a mark
          // shows what was written rather than an empty box that would silently
          // erase it on the next save.
          (setTender(data), setNote(data.feedback?.note ?? ''), setError(null)),
      )
      .catch((err) => {
        if (cancelled) return;
        setTender(null);
        setError(err instanceof ApiError ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId, resetTranslation]);

  const flash = useCallback((what: string) => {
    setCopied(what);
    window.setTimeout(() => setCopied(null), 1600);
  }, []);

  /**
   * Record or withdraw a verdict, then re-read the notice.
   *
   * The second call is deliberate rather than lazy: marking one notice can
   * change what the learner concludes about *this* one too, and the response to
   * a POST carries the model, not the tender. Re-reading gets the authoritative
   * `hidden` and the reasons behind it instead of a guess assembled here.
   */
  const mark = useCallback(
    async (verdict: Verdict | null) => {
      if (tenderId === null) return;
      setMarking(true);
      setMarkError(null);
      try {
        const result =
          verdict === null
            ? await api.clearFeedback(tenderId)
            : await api.setFeedback(tenderId, verdict, note.trim() || undefined);
        const fresh = await api.tender(tenderId);
        setTender(fresh);
        setNote(fresh.feedback?.note ?? '');
        onFeedback(result);
      } catch (err) {
        setMarkError(err instanceof ApiError ? err.message : String(err));
      } finally {
        setMarking(false);
      }
    },
    [tenderId, note, onFeedback],
  );

  /**
   * Fetch the English text for this notice, once.
   *
   * Guarded on `shownId` rather than a `cancelled` flag, and the difference is
   * the reason: this is a callback, not an effect, so there is no cleanup to
   * flip. j/k moves to the next notice without closing the panel, so a reply
   * arriving after the move would drop one notice's English onto another's.
   *
   * No optimistic state and no retry loop. The button says what it is doing, and
   * a failure leaves the original text on screen with the reason underneath -
   * the reader can press it again.
   */
  const translate = useCallback(async () => {
    if (tenderId === null) return;
    const requestedFor = tenderId;
    setTranslating(true);
    setTranslateError(null);
    try {
      const result = await api.translate(requestedFor);
      // The notice moved on while this was in flight. Dropping the answer is
      // right: the loader effect has already cleared this state for the notice
      // now on screen, and writing to it would put this Portuguese notice's
      // English under a different one.
      if (shownId.current !== requestedFor) return;
      setTranslation(result);
      setShowOriginal(false);
    } catch (err) {
      if (shownId.current !== requestedFor) return;
      setTranslateError(err instanceof ApiError ? err.message : String(err));
    } finally {
      if (shownId.current === requestedFor) setTranslating(false);
    }
  }, [tenderId]);

  // Split deliberately into two effects.
  //
  // Opener capture, initial focus, the scroll lock and focus restoration depend
  // only on whether the panel is open. Keyed on the callbacks too — which
  // Dashboard recreates as inline arrows on every render — this effect tore down
  // and re-ran constantly: focus was yanked back to the first control while the
  // reader was tabbing, and `opener` was overwritten with something inside the
  // panel, so closing restored focus to a node that no longer existed.
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const panel = document.getElementById('detail-panel');
    panel?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
      opener?.focus?.();
    };
  }, [open]);

  // The handler reads its callbacks through a ref, so it is installed once per
  // open and still always calls the current ones.
  const handlers = useRef({ onClose, onNext, onPrev, hasNext, hasPrev });
  handlers.current = { onClose, onNext, onPrev, hasNext, hasPrev };

  useEffect(() => {
    if (!open) return;
    const panel = document.getElementById('detail-panel');
    const handle = (event: KeyboardEvent) => {
      const {
        onClose: close,
        onNext: next,
        onPrev: prev,
        hasNext: canNext,
        hasPrev: canPrev,
      } = handlers.current;
      if (event.key === 'Escape') {
        close();
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
      if (event.key === 'j' && canNext) {
        event.preventDefault();
        next();
      } else if (event.key === 'k' && canPrev) {
        event.preventDefault();
        prev();
      }
    };
    document.addEventListener('keydown', handle);
    return () => document.removeEventListener('keydown', handle);
  }, [open]);

  const sub = tender
    ? {
        topic: tender.topic_relevance_score,
        product: tender.product_fit_score,
        intent: tender.procurement_intent_score,
      }
    : { topic: 0, product: 0, intent: 0 };
  // The weighted sum is reproducible here; the final score is not. The engine
  // applies caps and non-actionable multipliers and *then* rounds with Python's
  // round(), which is half-to-even where JS Math.round is half-up. Inferring a
  // cap from the difference invented one on every notice whose weighted score
  // landed on .5 — 64 of 283 stored notices claimed a cap that did not exist.
  // The claim is now made only where there is evidence for it.
  const weighted = 0.55 * sub.topic + 0.3 * sub.product + 0.15 * sub.intent;
  const cappedByDisqualifier = Boolean(tender && tender.disqualifiers.length > 0);
  const scaledByStatus = Boolean(tender && !tender.is_actionable);
  const reduced = cappedByDisqualifier || scaledByStatus;
  // Within half a point is rounding, not a cap. Beyond that with no disqualifier
  // and no status reason, say nothing rather than guess.
  const unexplainedGap = tender ? Math.abs(weighted - tender.relevance_score) > 0.5 : false;
  const noticeHref = safeHref(tender?.source_url);
  const urgency = tender ? deadlineUrgency(tender.deadline) : null;

  return (
    <aside
      id="detail-panel"
      className={`drawer drawer--detail${open ? ' is-on' : ''}`}
      role="dialog"
      aria-modal={open}
      aria-label="Tender detail"
      aria-hidden={!open}
      style={open ? undefined : { visibility: 'hidden' }}
    >
      <header className="drawer__head">
        {tender ? (
          <span
            className={`score score--${TONE_CLASS[scoreTone(tender.relevance_score, bands)]} num`}
            style={{ flex: 'none' }}
          >
            {tender.relevance_score}
          </span>
        ) : null}
        <div style={{ minWidth: 0 }}>
          <h2 className="detail__title">
            {tender?.title ?? (error ? 'Could not load this tender' : 'Loading…')}
          </h2>
          {tender ? (
            <p>
              {tender.buyer_name ?? 'Buyer not published'} · {sourceLabel(tender.source)} ·{' '}
              <span className="mono">{tender.reference_number ?? tender.source_notice_id}</span>
            </p>
          ) : null}
        </div>
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
          <button type="button" className="btn btn--icon" onClick={onClose} aria-label="Close">
            <Icon name="close" size={16} />
          </button>
        </div>
      </header>

      <div className="drawer__body">
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
                <span className="badge badge--grey">
                  {tender.relevance_category.replace(/_/g, ' ')}
                </span>
              ) : null}
              <span className="badge badge--grey">Found {formatDate(tender.first_seen_at)}</span>
              {tender.hidden ? <span className="badge badge--grey">Hidden</span> : null}
            </div>

            {/* First, because it is why the drawer was opened: the reader is
                deciding. Both directions are here, unlike the card, and "keep"
                does more than it appears to - it protects this notice's wording
                from ever being used to hide anything. */}
            <section className="dsection">
              <h3>Is this one relevant?</h3>
              <p className="muted">
                {tender.feedback
                  ? tender.feedback.verdict === 'irrelevant'
                    ? 'Marked not relevant, so it is hidden from the working views and its wording teaches the system what to hide.'
                    : 'Marked relevant. Nothing in its wording will be used to hide another notice.'
                  : 'Nobody has decided yet. Marking it teaches the system, and either mark can be withdrawn.'}
              </p>

              <div className="verdict__row">
                <button
                  type="button"
                  className={`btn${tender.feedback?.verdict === 'relevant' ? ' btn--primary' : ''}`}
                  disabled={marking}
                  aria-pressed={tender.feedback?.verdict === 'relevant'}
                  onClick={() => void mark('relevant')}
                >
                  <Icon name="check" size={13} />
                  Relevant
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={marking}
                  aria-pressed={tender.feedback?.verdict === 'irrelevant'}
                  onClick={() => void mark('irrelevant')}
                >
                  <Icon name="block" size={13} />
                  Not relevant
                </button>
                {tender.feedback ? (
                  <button
                    type="button"
                    className="btn btn--ghost"
                    disabled={marking}
                    onClick={() => void mark(null)}
                  >
                    <Icon name="refresh" size={13} />
                    Withdraw
                  </button>
                ) : null}
              </div>

              <label className="sr" htmlFor="verdictNote">
                Why, in a few words
              </label>
              <input
                className="input"
                id="verdictNote"
                type="text"
                maxLength={2000}
                value={note}
                disabled={marking}
                placeholder="Why? (optional — saved with the mark)"
                onChange={(event) => setNote(event.target.value)}
              />

              {markError ? (
                <p className="notice notice--bad" role="alert">
                  <Icon name="warn" size={13} />
                  {markError}
                </p>
              ) : null}

              {/* Only when the machine made the call. Every pattern that hid it
                  is listed, because a hide nobody can argue with is a hide
                  nobody will trust. */}
              {tender.auto_irrelevant && tender.auto_irrelevant_reasons.length ? (
                <>
                  <h4 className="subhead">Hidden by what was learned</h4>
                  <ul className="reasons reasons--flag">
                    {tender.auto_irrelevant_reasons.map((item) => (
                      <li key={item}>
                        <Icon name="block" size={13} />
                        {item}
                      </li>
                    ))}
                  </ul>
                  <p className="muted">
                    Marking it relevant overrides this, and stops its wording hiding anything else.
                  </p>
                </>
              ) : null}
            </section>

            <section className="dsection">
              <h3>Why this scores {tender.relevance_score}</h3>
              <p className="formula">
                <b>0.55</b> × topic <b>{sub.topic}</b> &nbsp;+&nbsp; <b>0.30</b> × product and
                hosting <b>{sub.product}</b> &nbsp;+&nbsp; <b>0.15</b> × intent <b>{sub.intent}</b>
                &nbsp;=&nbsp; <b>{weighted.toFixed(2)}</b> &nbsp;→&nbsp;{' '}
                <b>{tender.relevance_score}</b>
                {reduced ? (
                  <>
                    , reduced{' '}
                    {cappedByDisqualifier
                      ? 'by the disqualifier below'
                      : 'because this notice is no longer open'}
                    .
                  </>
                ) : unexplainedGap ? (
                  '.'
                ) : (
                  '. Nothing capped it.'
                )}
              </p>

              <div className="meters">
                {WEIGHTS.map((row) => (
                  <div className="meter" key={row.key}>
                    <div className="meter__top">
                      <span>
                        {row.label} <span className="meter__weight">× {row.weight.toFixed(2)}</span>
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
                  <ul className="reasons reasons--flag">
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

            <section className="dsection">
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
                  <dd>{sourceLabel(tender.source)}</dd>
                </div>
              </dl>
            </section>

            {tender.classification_codes.length ? (
              <section className="dsection">
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
              <section className="dsection">
                <h3>Description</h3>
                <p className="desc">
                  {translation && !showOriginal ? translation.text : tender.description}
                </p>
                {tender.needs_translation ? (
                  <div className="translate">
                    {translation ? (
                      <>
                        <p className="translate__note">
                          {showOriginal
                            ? `Original text, in ${languageLabel(translation.source_language)}.`
                            : `Translated from ${languageLabel(translation.source_language)} by machine — read the original notice before relying on it.`}
                        </p>
                        <button
                          type="button"
                          className="btn btn--primary"
                          onClick={() => setShowOriginal((shown) => !shown)}
                        >
                          {showOriginal ? 'Show English' : 'Show original'}
                          <Icon name="translate" size={13} />
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="btn btn--primary"
                        onClick={translate}
                        disabled={translating}
                      >
                        {translating ? 'Translating…' : 'Translate'}
                        <Icon name="translate" size={13} />
                      </button>
                    )}
                    {translateError ? (
                      <p className="notice notice--bad" role="alert">
                        {translateError}
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </section>
            ) : null}

            <section className="dsection">
              <h3>Links</h3>
              <div className="linklist">
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
              <section className="dsection">
                <details className="raw">
                  <summary>
                    <Icon name="chevronRight" size={12} />
                    Raw source data
                  </summary>
                  <div className="raw__actions">
                    <button
                      type="button"
                      className="btn btn--ghost"
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

      <footer className="drawer__foot">
        <span className="count num">
          {position ? (
            <>
              {position.index} of {position.total} · <span className="kbd">j</span>{' '}
              <span className="kbd">k</span> to move
            </>
          ) : null}
        </span>
        <button
          type="button"
          className="btn btn--ghost"
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
