import type { DeploymentFit, FitStatus, Stats, SourceStatus, TenderFilters } from '../types';
import { CATEGORY_LABELS, DEPLOYMENT_LABELS, FIT_LABELS, SORT_OPTIONS } from '../labels';

const FIT_ORDER: FitStatus[] = ['high_fit', 'good_fit', 'possible_fit', 'manual_review', 'not_fit'];
const DEPLOYMENT_ORDER: DeploymentFit[] = [
  'cloud_required',
  'cloud_preferred',
  'cloud_allowed',
  'deployment_unspecified',
  'hybrid',
  'mandatory_on_premises',
  'offline_or_air_gapped',
];

interface Props {
  filters: TenderFilters;
  stats: Stats | null;
  sources: SourceStatus[];
  onChange: (patch: Partial<TenderFilters>) => void;
  onReset: () => void;
}

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export function FilterPanel({ filters, stats, sources, onChange, onReset }: Props) {
  const categories = stats?.categories.map((c) => c.key) ?? Object.keys(CATEGORY_LABELS);
  return (
    <aside className="filters" aria-label="Filters">
      <div className="filters__row">
        <h2>Filters</h2>
        <button className="link" onClick={onReset}>
          Reset
        </button>
      </div>

      <label className="field">
        <span>Search</span>
        <input
          type="search"
          value={filters.query}
          placeholder="safety data sheet, EHS, incident…"
          onChange={(e) => onChange({ query: e.target.value })}
        />
      </label>

      <label className="field">
        <span>
          Minimum relevance score: <strong>{filters.minimum_score}</strong>
        </span>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={filters.minimum_score}
          onChange={(e) => onChange({ minimum_score: Number(e.target.value) })}
        />
      </label>

      <label className="field field--check">
        <input
          type="checkbox"
          checked={filters.active_only}
          onChange={(e) => onChange({ active_only: e.target.checked })}
        />
        <span>Open opportunities only</span>
      </label>

      <fieldset className="chips">
        <legend>Fit status</legend>
        {FIT_ORDER.map((fit) => (
          <button
            key={fit}
            type="button"
            className={`chip ${filters.fit_statuses.includes(fit) ? 'is-on' : ''}`}
            onClick={() => onChange({ fit_statuses: toggle(filters.fit_statuses, fit) })}
          >
            {FIT_LABELS[fit]}
          </button>
        ))}
      </fieldset>

      <fieldset className="chips">
        <legend>Deployment fit</legend>
        {DEPLOYMENT_ORDER.map((dep) => (
          <button
            key={dep}
            type="button"
            className={`chip ${filters.deployment_fits.includes(dep) ? 'is-on' : ''}`}
            onClick={() => onChange({ deployment_fits: toggle(filters.deployment_fits, dep) })}
          >
            {DEPLOYMENT_LABELS[dep]}
          </button>
        ))}
      </fieldset>

      <fieldset className="chips">
        <legend>Capability</legend>
        {categories.map((key) => (
          <button
            key={key}
            type="button"
            className={`chip ${filters.categories.includes(key) ? 'is-on' : ''}`}
            onClick={() => onChange({ categories: toggle(filters.categories, key) })}
          >
            {CATEGORY_LABELS[key] ?? key}
          </button>
        ))}
      </fieldset>

      <label className="field">
        <span>Source</span>
        <select
          multiple
          size={Math.min(6, Math.max(3, sources.length))}
          value={filters.sources}
          onChange={(e) =>
            onChange({ sources: Array.from(e.target.selectedOptions, (o) => o.value) })
          }
        >
          {sources.map((source) => (
            <option key={source.name} value={source.name}>
              {source.display_name} ({source.tender_count})
            </option>
          ))}
        </select>
      </label>

      <div className="filters__pair">
        <label className="field">
          <span>Country</span>
          <select
            value={filters.countries[0] ?? ''}
            onChange={(e) => onChange({ countries: e.target.value ? [e.target.value] : [] })}
          >
            <option value="">All</option>
            {(stats?.countries ?? []).map((country) => (
              <option key={country} value={country}>
                {country}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Status</span>
          <select
            value={filters.statuses[0] ?? ''}
            onChange={(e) => onChange({ statuses: e.target.value ? [e.target.value] : [] })}
          >
            <option value="">All</option>
            {(stats?.statuses ?? []).map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="filters__pair">
        <label className="field">
          <span>Deadline before</span>
          <input
            type="date"
            value={filters.deadline_to}
            onChange={(e) => onChange({ deadline_to: e.target.value })}
          />
        </label>
        <label className="field">
          <span>Sort</span>
          <select value={filters.sort} onChange={(e) => onChange({ sort: e.target.value })}>
            {SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>
    </aside>
  );
}
