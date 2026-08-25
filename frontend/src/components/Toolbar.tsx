import type { SortOption, TenderFilters } from '../types';
import { Icon } from './Icon';

const SORTS: { value: SortOption; label: string }[] = [
  { value: 'score_desc', label: 'Relevance — high first' },
  { value: 'deadline_asc', label: 'Deadline — soonest first' },
  { value: 'published_desc', label: 'Published — newest first' },
  { value: 'first_seen_desc', label: 'Recently discovered' },
  { value: 'score_asc', label: 'Relevance — low first' },
];

/**
 * The sticky control surface: search, sort, and the active-filter chips.
 *
 * Sticky because it is what you steer the list with, and scrolling a long list
 * away from its own controls is the thing that makes a results page feel like a
 * document instead of a tool.
 *
 * The bucket tabs used to live here. They are gone: they were a third way of
 * saying what the sidebar lenses and the chips already say.
 */
export function Toolbar({
  filters,
  filterCount,
  onSearch,
  onSort,
  onClearAll,
  onOpenFilters,
  chips,
}: {
  filters: TenderFilters;
  /** Badged on the Filters button, so a narrowed list is visible when shut. */
  filterCount: number;
  onSearch: (query: string) => void;
  onSort: (sort: SortOption) => void;
  onClearAll: () => void;
  onOpenFilters: () => void;
  /**
   * What is narrowing the list. A locked chip is the lens's own predicate:
   * it explains why the list is short but cannot be removed, because the lens
   * is where the reader *is* — it changes by navigating, not by dismissal.
   */
  chips: { label: string; locked?: boolean; onRemove: () => void }[];
}) {
  const removable = chips.filter((chip) => !chip.locked);

  return (
    <div className="toolbar">
      <div className="toolbar__row">
        <div className="search">
          <span className="search__icon">
            <Icon name="search" size={15} />
          </span>
          <input
            className="input"
            type="search"
            value={filters.query}
            placeholder="Search title, description, buyer or reference…"
            aria-label="Search tenders"
            onChange={(event) => onSearch(event.target.value)}
          />
          {filters.query ? (
            <button
              type="button"
              className="search__clear"
              aria-label="Clear search"
              onClick={() => onSearch('')}
            >
              ✕
            </button>
          ) : null}
        </div>

        <button type="button" className="btn" onClick={onOpenFilters} title="Refine this view">
          <Icon name="sliders" size={14} />
          Filters
          {filterCount > 0 ? <span className="btn__n">{filterCount}</span> : null}
        </button>

        <div className="sortwrap">
          <label className="sr" htmlFor="sortSel">
            Sort results
          </label>
          <select
            className="select"
            id="sortSel"
            value={filters.sort}
            onChange={(event) => onSort(event.target.value as SortOption)}
          >
            {SORTS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {chips.length > 0 ? (
        <div className="filterbar">
          <span className="filterbar__label">Active</span>
          {chips.map((chip) =>
            chip.locked ? (
              <span className="fchip fchip--locked" key={chip.label} title="Set by the current view">
                {chip.label}
              </span>
            ) : (
              <span className="fchip" key={chip.label}>
                {chip.label}
                <button
                  type="button"
                  className="fchip__x"
                  aria-label={`Remove filter: ${chip.label}`}
                  onClick={chip.onRemove}
                >
                  ✕
                </button>
              </span>
            ),
          )}
          {removable.length > 0 ? (
            <button type="button" className="btn btn--ghost btn--sm" onClick={onClearAll}>
              Clear all
            </button>
          ) : null}
        </div>
      ) : null}

    </div>
  );
}
