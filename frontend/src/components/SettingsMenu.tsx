import { useEffect, useRef } from 'react';
import { SETTINGS_CATEGORIES, type SettingsCategory, type SettingsKey } from '../state/settingsNav';
import { Icon } from './Icon';

/**
 * The category menu that opens off the rail's Settings button.
 *
 * A menu, not a panel: it holds no controls, only the way to them. Everything
 * that used to be one ten-section scroll now sits behind a named row, and the
 * row says what is inside so nobody has to open three to find the theme toggle.
 *
 * It pops *upward*, because the rail pins Settings to its bottom edge — a menu
 * dropping down from there would open off the bottom of the window.
 *
 * Keyboard behaviour is the whole reason this is a real menu rather than a
 * styled div: arrow keys move between rows, Home and End jump to the ends,
 * Escape closes and hands focus back to the button that opened it, and Tab
 * leaves entirely. Without that a mouse is the only way in.
 */
export function SettingsMenu({
  open,
  activeKey,
  filterCount,
  onSelect,
  onClose,
}: {
  open: boolean;
  /** Which category is already showing, so the menu can mark it. */
  activeKey: SettingsKey | null;
  /** Shown against Filters, so a narrowed list is visible from the menu. */
  filterCount: number;
  onSelect: (key: SettingsKey) => void;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement | null>(null);

  // Focus the active row on open, or the first one. Runs only on `open` so a
  // re-render while the reader is arrowing around does not yank focus back.
  useEffect(() => {
    if (!open) return;
    const items = rows(menuRef.current);
    const active = items.find((el) => el.dataset.active === 'true');
    (active ?? items[0])?.focus();
  }, [open]);

  // Close on anything that means "I am done here": Escape, a click outside, or
  // focus leaving the menu altogether.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    };
    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      // The trigger closes the menu itself; ignoring it here stops the two
      // handlers from fighting and re-opening it on the same click.
      if (menuRef.current?.contains(target)) return;
      if ((target as HTMLElement).closest?.('[data-settings-trigger]')) return;
      onClose();
    };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onPointer);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousedown', onPointer);
    };
  }, [open, onClose]);

  const onMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const items = rows(menuRef.current);
    if (!items.length) return;
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const move = (next: number) => {
      event.preventDefault();
      items[(next + items.length) % items.length]?.focus();
    };
    if (event.key === 'ArrowDown') move(index + 1);
    else if (event.key === 'ArrowUp') move(index - 1);
    else if (event.key === 'Home') move(0);
    else if (event.key === 'End') move(items.length - 1);
  };

  const groups = [...new Set(SETTINGS_CATEGORIES.map((c) => c.group))].sort();
  // Roving tabindex: exactly one row is tabbable, and it must exist even when no
  // category is active. Keying it only on `active` left every row at -1 on a
  // fresh load, so Tab skipped straight past the menu and the keyboard could not
  // get in at all.
  const anyActive = SETTINGS_CATEGORIES.some((c) => c.key === activeKey);
  const tabStopKey = anyActive ? activeKey : SETTINGS_CATEGORIES[0].key;

  return (
    <div
      ref={menuRef}
      id="settings-menu"
      className={`smenu${open ? ' is-on' : ''}`}
      role="menu"
      aria-label="Settings"
      aria-hidden={!open}
      onKeyDown={onMenuKeyDown}
    >
      <p className="smenu__head">Settings</p>

      {groups.map((group, groupIndex) => (
        <div className="smenu__group" key={group}>
          {groupIndex > 0 ? <hr className="smenu__rule" /> : null}
          {SETTINGS_CATEGORIES.filter((c) => c.group === group).map((category) => (
            <Row
              key={category.key}
              category={category}
              active={activeKey === category.key}
              tabbable={tabStopKey === category.key}
              badge={category.key === 'filters' && filterCount > 0 ? filterCount : null}
              onSelect={onSelect}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

function Row({
  category,
  active,
  tabbable,
  badge,
  onSelect,
}: {
  category: SettingsCategory;
  active: boolean;
  /** The single row Tab reaches; arrow keys move from there. */
  tabbable: boolean;
  badge: number | null;
  onSelect: (key: SettingsKey) => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={`smenu__item${active ? ' is-active' : ''}`}
      data-active={active}
      // Exactly one row is tabbable, so Tab enters the menu once and then leaves
      // it, rather than walking every row. Arrow keys move within.
      tabIndex={tabbable ? 0 : -1}
      onClick={() => onSelect(category.key)}
    >
      <span className="smenu__icon" aria-hidden="true">
        <Icon name={category.icon} size={17} />
      </span>
      <span className="smenu__text">
        <span className="smenu__label">{category.label}</span>
        <span className="smenu__blurb">{category.blurb}</span>
      </span>
      {badge !== null ? (
        <span className="smenu__badge">
          {badge}
          <span className="sr"> filters active</span>
        </span>
      ) : null}
      {/* A chevron only where the row leads somewhere that takes the width.
          Filters opens the panel beside the results, which is a different kind
          of move and should not promise the same thing. */}
      {category.surface === 'page' ? (
        <Icon name="chevronRight" size={13} className="smenu__chev" />
      ) : null}
    </button>
  );
}

function rows(root: HTMLDivElement | null): HTMLButtonElement[] {
  return root ? Array.from(root.querySelectorAll<HTMLButtonElement>('.smenu__item')) : [];
}
