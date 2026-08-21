import type { TenderFilters } from '../types';
import { SORT_OPTIONS, type FilterChip } from '../state/urlFilters';
import { Icon } from './Icon';

/**
 * Search + sort + one "Filters & settings" button with a live count (delta 3),
 * and every active filter below it as an individually removable chip.
 */
export interface ToolbarProps {
  filters: TenderFilters;
  chips: FilterChip[];
  activeCount: number;
  onChange: (patch: Partial<TenderFilters>) => void;
  onOpenSettings: () => void;
  onClearAll: () => void;
}

export function Toolbar({
  filters,
  chips,
  activeCount,
  onChange,
  onOpenSettings,
  onClearAll,
}: ToolbarProps) {
  return (
    <div className="toolbar">
      <div className="toolbar__row">
        <div className={`search${filters.query ? ' has-value' : ''}`}>
          <span className="search__icon">
            <Icon name="search" size={15} />
          </span>
          <input
            className="input"
            type="search"
            value={filters.query}
            placeholder="Search title, description, buyer or reference…"
            aria-label="Search tenders"
            onChange={(event) => onChange({ query: event.target.value })}
          />
          <button
            type="button"
            className="search__clear"
            aria-label="Clear search"
            onClick={() => onChange({ query: '' })}
          >
            ✕
          </button>
        </div>

        <div className="sortwrap">
          <label className="sr" htmlFor="sortSel">
            Sort results
          </label>
          <select
            className="select"
            id="sortSel"
            value={filters.sort}
            onChange={(event) => onChange({ sort: event.target.value as TenderFilters['sort'] })}
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        <div className="toolbar__sep" aria-hidden="true" />

        <button
          type="button"
          className="btn btn--primary toolbar__settings"
          onClick={onOpenSettings}
        >
          <Icon name="sliders" size={15} />
          Filters &amp; settings
          {activeCount > 0 ? <span className="btn__count">{activeCount}</span> : null}
        </button>
      </div>

      {chips.length > 0 ? (
        <div className="filterbar">
          <span className="filterbar__label">Active</span>
          {chips.map((chip) => (
            <span className="fchip" key={chip.key}>
              {chip.label}
              <button
                type="button"
                className="fchip__x"
                aria-label={`Remove filter: ${chip.label}`}
                onClick={() => onChange(chip.clear)}
              >
                ✕
              </button>
            </span>
          ))}
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClearAll}>
            Clear all
          </button>
        </div>
      ) : null}
    </div>
  );
}
