import { SORT_OPTIONS, type FilterChip } from '../state/urlFilters';
import type { TenderFilters } from '../types';
import { Icon } from './Icon';

/** Search, sort, and the filters disclosure. Nothing else earns a place here. */
export function Toolbar({
  filters,
  chips,
  filtersOpen,
  onChange,
  onToggleFilters,
}: {
  filters: TenderFilters;
  chips: FilterChip[];
  filtersOpen: boolean;
  onChange: (patch: Partial<TenderFilters>) => void;
  onToggleFilters: () => void;
}) {
  return (
    <div className="toolbar">
      <div className="search">
        <Icon name="search" size={15} />
        <input
          className="field"
          type="search"
          value={filters.query}
          placeholder="Search title, buyer or reference"
          aria-label="Search tenders"
          onChange={(event) => onChange({ query: event.target.value })}
        />
        {filters.query ? (
          <button
            type="button"
            className="search__clear"
            aria-label="Clear search"
            onClick={() => onChange({ query: '' })}
          >
            <Icon name="close" size={13} />
          </button>
        ) : null}
      </div>

      <span className="toolbar__spacer" />

      <label className="sr" htmlFor="sort">
        Sort results
      </label>
      <select
        className="select"
        id="sort"
        value={filters.sort}
        onChange={(event) => onChange({ sort: event.target.value as TenderFilters['sort'] })}
      >
        {SORT_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      <button
        type="button"
        className="btn btn--quiet"
        aria-expanded={filtersOpen}
        onClick={onToggleFilters}
      >
        <Icon name="sliders" size={15} />
        Filters
        {chips.length > 0 ? <span className="btn__n">{chips.length}</span> : null}
      </button>
    </div>
  );
}
