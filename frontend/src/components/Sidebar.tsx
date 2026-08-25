import type { Stats } from '../types';
import { LENSES, type LensContext, type LensKey } from '../state/lenses';
import { formatDateTime } from '../labels';
import { Icon } from './Icon';

const TONE_CLASS: Record<string, string> = {
  brand: 'dot--brand',
  good: 'dot--good',
  warning: 'dot--warning',
  serious: 'dot--serious',
};

export type SettingsScreen = 'rules' | 'sources';

/**
 * The permanent left column: navigation, settings, and the two operator
 * actions.
 *
 * It replaces the rail, the masthead and the stat-tile row at once, which is
 * where most of the old page's vertical budget went — the first tender used to
 * begin 541px down a 950px viewport.
 *
 * The lens counts are muted on purpose. They overlap by construction, so they
 * do not sum to the total, and rendering them as headline numerals invited
 * arithmetic that cannot reconcile.
 */
export function Sidebar({
  stats,
  lensContext,
  activeLens,
  settingsScreen,
  brokenSources,
  busy,
  sweeping,
  onSelectLens,
  onOpenSettings,
  onFetch,
  onRescore,
}: {
  stats: Stats | null;
  lensContext: LensContext;
  activeLens: LensKey | null;
  settingsScreen: SettingsScreen | null;
  /** Connectors reporting a problem — badged on Sources, where the fix is. */
  brokenSources: number;
  busy: 'fetch' | 'rescore' | null;
  sweeping: boolean;
  onSelectLens: (key: LensKey) => void;
  onOpenSettings: (screen: SettingsScreen) => void;
  onFetch: () => void;
  onRescore: () => void;
}) {
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="sidebar__brand">
        <span className="sidebar__mark" aria-hidden="true">
          TM
        </span>
        <span className="sidebar__name">Tender Monitor</span>
      </div>

      <p className="sidebar__group">Tenders</p>
      <ul className="sidebar__list">
        {LENSES.map((lens) => {
          const count = lens.count(stats);
          const disabled = lens.key === 'new' && !lensContext.lastRunAt;
          const on = settingsScreen === null && activeLens === lens.key;
          return (
            <li key={lens.key}>
              <button
                type="button"
                className={`navitem${on ? ' is-on' : ''}`}
                aria-current={on ? 'page' : undefined}
                disabled={disabled}
                title={disabled ? lens.unavailable : undefined}
                onClick={() => onSelectLens(lens.key)}
              >
                {lens.tone !== 'none' ? (
                  <span className={`dot ${TONE_CLASS[lens.tone]}`} aria-hidden="true" />
                ) : (
                  <span className="dot dot--none" aria-hidden="true" />
                )}
                <span className="navitem__label">{lens.label}</span>
                {typeof count === 'number' ? (
                  <span className="navitem__n">{count.toLocaleString('en-GB')}</span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>

      <p className="sidebar__group">Settings</p>
      <ul className="sidebar__list">
        <li>
          <button
            type="button"
            className={`navitem${settingsScreen === 'rules' ? ' is-on' : ''}`}
            aria-current={settingsScreen === 'rules' ? 'page' : undefined}
            onClick={() => onOpenSettings('rules')}
          >
            <span className="navitem__icon" aria-hidden="true">
              <Icon name="sliders" size={14} />
            </span>
            <span className="navitem__label">Matching rules</span>
          </button>
        </li>
        <li>
          <button
            type="button"
            className={`navitem${settingsScreen === 'sources' ? ' is-on' : ''}`}
            aria-current={settingsScreen === 'sources' ? 'page' : undefined}
            onClick={() => onOpenSettings('sources')}
          >
            <span className="navitem__icon" aria-hidden="true">
              <Icon name="sliders" size={14} />
            </span>
            <span className="navitem__label">Sources</span>
            {brokenSources > 0 ? (
              <span
                className="navitem__warn"
                title={`${brokenSources} source${brokenSources === 1 ? '' : 's'} reporting a problem`}
              >
                {brokenSources}
              </span>
            ) : null}
          </button>
        </li>
      </ul>

      <div className="sidebar__foot">
        <p className="sidebar__last">
          {stats?.last_successful_fetch ? (
            <>
              Last sweep
              <b>{formatDateTime(stats.last_successful_fetch)}</b>
            </>
          ) : (
            <>
              No successful sweep yet
              <b>—</b>
            </>
          )}
        </p>
        <div className="sidebar__actions">
          <button
            type="button"
            className="btn btn--primary btn--sm"
            disabled={busy !== null || sweeping}
            onClick={onFetch}
            title="Query all enabled sources now"
          >
            <Icon name="download" size={13} />
            {sweeping ? 'Sweeping…' : 'Fetch'}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            disabled={busy !== null}
            onClick={onRescore}
            title="Reload the relevance profile and re-score every stored notice"
          >
            <Icon name="refresh" size={13} />
            {busy === 'rescore' ? 'Re-scoring…' : 'Re-score'}
          </button>
        </div>
      </div>
    </nav>
  );
}
