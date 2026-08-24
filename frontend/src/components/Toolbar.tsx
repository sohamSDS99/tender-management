import type { SortOption, Stats, TenderFilters } from '../types';
import { VIEWS, type ViewContext, type ViewKey } from '../state/views';
import { Icon } from './Icon';

const SORTS: { value: SortOption; label: string }[] = [
  { value: 'score_desc', label: 'Relevance — high first' },
  { value: 'deadline_asc', label: 'Deadline — soonest first' },
  { value: 'published_desc', label: 'Published — newest first' },
  { value: 'first_seen_desc', label: 'Recently discovered' },
  { value: 'score_asc', label: 'Relevance — low first' },
];

const TONE_CLASS: Record<string, string> = {
  brand: 'dot--brand',
  good: 'dot--good',
  critical: 'dot--critical',
};

/**
 * The sticky control surface: search, sort, the Settings toggle, the active-filter
 * chips, and the bucket tabs.
 *
 * Sticky because it is what you steer the list with, and scrolling a long list
 * away from its own controls is the thing that makes a results page feel like a
 * document instead of a tool.
 */
export function Toolbar({
  filters,
  stats,
  viewContext,
  activeView,
  bucketCounts,
  activeFilterCount,
  settingsOpen,
  onSearch,
  onSort,
  onToggleSettings,
  onSelectView,
  onClearAll,
  chips,
}: {
  filters: TenderFilters;
  stats: Stats | null;
  viewContext: ViewContext;
  activeView: ViewKey | null;
  bucketCounts: Partial<Record<ViewKey, number | null>>;
  activeFilterCount: number;
  settingsOpen: boolean;
  onSearch: (query: string) => void;
  onSort: (sort: SortOption) => void;
  onToggleSettings: () => void;
  onSelectView: (key: ViewKey) => void;
  onClearAll: () => void;
  /** Removable summaries of what is currently narrowing the list. */
  chips: { label: string; onRemove: () => void }[];
}) {
  const total = stats?.total_tenders ?? null;

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

        <div className="toolbar__sep" aria-hidden="true" />

        <button
          type="button"
          className={`btn${settingsOpen ? '' : ' btn--primary'}`}
          aria-expanded={settingsOpen}
          onClick={onToggleSettings}
        >
          <Icon name="settings" size={15} />
          Settings
          {activeFilterCount > 0 ? <span className="btn__count">{activeFilterCount}</span> : null}
        </button>
      </div>

      {chips.length > 0 ? (
        <div className="filterbar">
          <span className="filterbar__label">Active</span>
          {chips.map((chip) => (
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
          ))}
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClearAll}>
            Clear all
          </button>
        </div>
      ) : null}

      <div className="tabsbar" role="tablist" aria-label="Tender buckets">
        {VIEWS.map((view) => {
          const count = bucketCounts[view.key];
          const disabled = view.key === 'new' && !viewContext.lastRunAt;
          return (
            <button
              key={view.key}
              type="button"
              role="tab"
              className={`tab${activeView === view.key ? ' is-on' : ''}`}
              aria-selected={activeView === view.key}
              disabled={disabled}
              title={disabled ? 'No sweep has run yet' : undefined}
              onClick={() => onSelectView(view.key)}
            >
              {view.tone !== 'none' ? (
                <span className={`dot ${TONE_CLASS[view.tone]}`} aria-hidden="true" />
              ) : null}
              {view.label}
              {typeof count === 'number' ? (
                <span className="tab__n">{count.toLocaleString('en-GB')}</span>
              ) : null}
            </button>
          );
        })}
        <span className="tabsbar__note">
          {total === null
            ? 'Nothing is discarded — every notice is stored and scored'
            : `Nothing is discarded — all ${total.toLocaleString('en-GB')} stored and scored`}
        </span>
      </div>
    </div>
  );
}
