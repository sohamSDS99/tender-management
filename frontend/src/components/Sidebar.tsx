import type { AutomationStatus, Stats, User } from '../types';
import { initials, type AuthStatus } from '../state/auth';
import { LENSES, type LensContext, type LensKey } from '../state/lenses';
import { SETTINGS_CATEGORIES, type SettingsKey } from '../state/settingsNav';
import { SWEEP_DEPTHS, formatDateTime, isSweepInFlight } from '../labels';
import { Icon } from './Icon';

const TONE_CLASS: Record<string, string> = {
  brand: 'dot--brand',
  good: 'dot--good',
  warning: 'dot--warning',
  serious: 'dot--serious',
};

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
  automation,
  lensContext,
  activeLens,
  settingsKey,
  brokenSources,
  busy,
  sweepDays,
  user,
  authStatus,
  onSweepDays,
  onSelectLens,
  onSelectCategory,
  onFetch,
  onRescore,
  onSignIn,
  onSignOut,
}: {
  stats: Stats | null;
  automation: AutomationStatus | null;
  lensContext: LensContext;
  activeLens: LensKey | null;
  settingsKey: SettingsKey | null;
  /** Connectors reporting a problem — badged on Sources, where the fix is. */
  brokenSources: number;
  busy: 'fetch' | 'rescore' | null;
  /** Days of history the next sweep will search. */
  sweepDays: number;
  /** The signed-in account, or null. Nothing in this column is gated on it. */
  user: User | null;
  authStatus: AuthStatus;
  onSweepDays: (days: number) => void;
  onSelectLens: (key: LensKey) => void;
  onSelectCategory: (key: SettingsKey) => void;
  onFetch: () => void;
  onRescore: () => void;
  onSignIn: () => void;
  onSignOut: () => void;
}) {
  // Shared helper, so the button's idea of "still sweeping" can never drift
  // from the one driving the progress poll.
  const sweeping = busy === 'fetch' || isSweepInFlight(automation?.last_run?.status);

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
          const count = lens.count(stats, automation);
          const disabled = lens.key === 'new' && !lensContext.lastRunAt;
          const on = settingsKey === null && activeLens === lens.key;
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
        {SETTINGS_CATEGORIES.map((category) => {
          const on = settingsKey === category.key;
          return (
            <li key={category.key}>
              <button
                type="button"
                className={`navitem${on ? ' is-on' : ''}`}
                aria-current={on ? 'page' : undefined}
                title={category.blurb}
                onClick={() => onSelectCategory(category.key)}
              >
                <span className="navitem__icon" aria-hidden="true">
                  <Icon name={category.icon} size={14} />
                </span>
                <span className="navitem__label">{category.label}</span>
                {category.key === 'sources' && brokenSources > 0 ? (
                  <span
                    className="navitem__warn"
                    title={`${brokenSources} source${brokenSources === 1 ? '' : 's'} reporting a problem`}
                  >
                    {brokenSources}
                  </span>
                ) : null}
              </button>
            </li>
          );
        })}
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
        <div className="depth">
          <span className="depth__label" id="sweepDepthLabel">
            Search back
          </span>
          <div className="seg seg--sm" role="group" aria-labelledby="sweepDepthLabel">
            {SWEEP_DEPTHS.map((days) => (
              <button
                key={days}
                type="button"
                className={sweepDays === days ? 'is-on' : undefined}
                aria-pressed={sweepDays === days}
                disabled={sweeping}
                aria-label={`Search back ${days} days`}
                onClick={() => onSweepDays(days)}
              >
                {days}d
              </button>
            ))}
          </div>
        </div>

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

        {/*
          The account control, pinned below the operator actions.

          Two states and no popup menu between them. A menu would need an
          outside-click handler, a focus trap and an escape key for two
          destinations, and the two destinations fit here as buttons: the chip
          opens the profile page, the icon signs out. Signed out it is a single
          button, because there is nothing to show about a reader who has not
          told us anything.

          `authStatus` is checked so this does not flash "Sign in" for the
          fraction of a second before the first reply lands — the one thing that
          would make an optional feature look like a wall.
        */}
        {authStatus === 'loading' ? (
          <div className="acctchip acctchip--wait" aria-hidden="true" />
        ) : user ? (
          <div className="acctchip">
            <button
              type="button"
              className={`acctchip__who${settingsKey === 'account' ? ' is-on' : ''}`}
              aria-current={settingsKey === 'account' ? 'page' : undefined}
              title="Your profile"
              onClick={() => onSelectCategory('account')}
            >
              <span className="acctchip__avatar" aria-hidden="true">
                {initials(user)}
              </span>
              <span className="acctchip__text">
                <b>{user.display_name}</b>
                <small>{user.role === 'admin' ? 'Administrator' : 'Member'}</small>
              </span>
            </button>
            <button
              type="button"
              className="btn btn--icon btn--sm"
              title={`Sign out of ${user.email}`}
              onClick={onSignOut}
            >
              <Icon name="signout" size={14} />
              <span className="sr">Sign out</span>
            </button>
          </div>
        ) : authStatus === 'unreachable' ? null : (
          <button type="button" className="btn btn--sm acctchip__signin" onClick={onSignIn}>
            <Icon name="user" size={13} />
            Sign in
          </button>
        )}
      </div>
    </nav>
  );
}
