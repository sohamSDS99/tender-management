import { Icon } from './Icon';

/**
 * The permanent left rail.
 *
 * The only chrome on the page that never moves or collapses, which is what makes
 * Settings findable: the previous version put it behind a toolbar button inside a
 * collapsed section, and it could not be found at all.
 *
 * Settings sits at the *bottom*, separated from the mark at the top by a flex gap
 * rather than by spacing that would drift as the rail grows. That is the
 * conventional home for configuration in a rail, and it keeps the top free for
 * anything navigational added later.
 */
export function Rail({
  settingsOpen,
  activeFilterCount,
  onToggleSettings,
}: {
  settingsOpen: boolean;
  /** Shown as a badge, so a narrowed list is visible even with the panel shut. */
  activeFilterCount: number;
  onToggleSettings: () => void;
}) {
  return (
    <nav className="rail" aria-label="Main">
      <span className="rail__mark" aria-hidden="true">
        TM
      </span>

      <span className="rail__gap" aria-hidden="true" />

      <button
        type="button"
        className={`railbtn${settingsOpen ? ' is-on' : ''}`}
        aria-expanded={settingsOpen}
        aria-controls="settings-slideout"
        title={settingsOpen ? 'Hide settings' : 'Show settings'}
        onClick={onToggleSettings}
      >
        <Icon name="settings" size={19} />
        Settings
        {activeFilterCount > 0 ? (
          <span className="railbtn__count">
            {activeFilterCount}
            <span className="sr"> filters active</span>
          </span>
        ) : null}
      </button>
    </nav>
  );
}
