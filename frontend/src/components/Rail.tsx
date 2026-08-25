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
 *
 * The button now opens a **menu of categories** rather than the settings panel
 * itself. Everything used to live in one ten-section scroll, so finding the theme
 * toggle meant scrolling past seven groups of filters; the categories give each
 * of those a name and one row.
 */
export function Rail({
  menuOpen,
  activeCount,
  onToggleMenu,
}: {
  /** Whether the category menu is showing. */
  menuOpen: boolean;
  /** Filters currently narrowing the list, badged so it is visible from the rail. */
  activeCount: number;
  onToggleMenu: () => void;
}) {
  return (
    <nav className="rail" aria-label="Main">
      <span className="rail__mark" aria-hidden="true">
        TM
      </span>

      <span className="rail__gap" aria-hidden="true" />

      <button
        type="button"
        // Read by the menu's outside-click handler, so clicking the trigger while
        // the menu is open closes it instead of the two handlers fighting and
        // re-opening it on the same click.
        data-settings-trigger="true"
        className={`railbtn${menuOpen ? ' is-on' : ''}`}
        aria-expanded={menuOpen}
        aria-haspopup="menu"
        aria-controls="settings-menu"
        title={menuOpen ? 'Hide settings' : 'Settings'}
        onClick={onToggleMenu}
      >
        <Icon name="settings" size={19} />
        Settings
        {activeCount > 0 ? (
          <span className="railbtn__count">
            {activeCount}
            <span className="sr"> filters active</span>
          </span>
        ) : null}
      </button>
    </nav>
  );
}
