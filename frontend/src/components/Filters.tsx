import type {
  CountBucket,
  DeploymentFit,
  FitStatus,
  SourceStatus,
  Stats,
  TenderFilters,
} from '../types';
import { DEFAULT_FILTERS, DEPLOYMENT_FITS, FIT_STATUSES } from '../state/urlFilters';
import { deploymentLabel, fitLabel } from '../labels';

/**
 * Filters, inline and collapsed by default.
 *
 * Not a drawer and not a modal: this task needs neither interruption nor
 * protected focus, and the results stay visible underneath while you narrow
 * them — which is the whole point. The previous version hid all of this behind
 * a 430px overlay that covered the very list it was filtering.
 */
export function Filters({
  filters,
  stats,
  sources,
  total,
  onChange,
  onReset,
  onClose,
}: {
  filters: TenderFilters;
  stats: Stats | null;
  sources: SourceStatus[];
  total: number;
  onChange: (patch: Partial<TenderFilters>) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const count = (buckets: CountBucket[] | undefined, key: string): number | null => {
    if (!buckets) return null;
    return buckets.find((b) => b.key === key)?.count ?? 0;
  };

  const toggle = <T extends string>(list: T[], value: T): T[] =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  const isDefault = JSON.stringify(filters) === JSON.stringify(DEFAULT_FILTERS);

  return (
    <div className="filters">
      <div className="fgroup">
        <h3>Minimum score</h3>
        <div className="rangerow">
          <span>At least</span>
          <b>{filters.minimum_score}</b>
        </div>
        <input
          className="range"
          type="range"
          min={0}
          max={100}
          step={5}
          value={filters.minimum_score}
          aria-label="Minimum relevance score"
          onChange={(event) => onChange({ minimum_score: Number(event.target.value) })}
        />
        <label className="switchrow">
          <input
            type="checkbox"
            checked={filters.active_only}
            onChange={(event) => onChange({ active_only: event.target.checked })}
          />
          Open opportunities only
        </label>
        <label className="switchrow">
          <input
            type="checkbox"
            checked={filters.has_deadline === true}
            onChange={(event) => onChange({ has_deadline: event.target.checked ? true : null })}
          />
          Has a published deadline
        </label>
      </div>

      <div className="fgroup">
        <h3>Fit</h3>
        <div className="chips">
          {FIT_STATUSES.map((value) => (
            <button
              key={value}
              type="button"
              className={`chip${filters.fit_statuses.includes(value) ? ' is-on' : ''}`}
              aria-pressed={filters.fit_statuses.includes(value)}
              onClick={() =>
                onChange({ fit_statuses: toggle<FitStatus>(filters.fit_statuses, value) })
              }
            >
              {fitLabel(value)}
              {count(stats?.by_fit_status, value) !== null ? (
                <span className="chip__n">{count(stats?.by_fit_status, value)}</span>
              ) : null}
            </button>
          ))}
        </div>
      </div>

      <div className="fgroup">
        <h3>Hosting</h3>
        <div className="chips">
          {DEPLOYMENT_FITS.map((value) => (
            <button
              key={value}
              type="button"
              className={`chip${filters.deployment_fits.includes(value) ? ' is-on' : ''}`}
              aria-pressed={filters.deployment_fits.includes(value)}
              onClick={() =>
                onChange({ deployment_fits: toggle<DeploymentFit>(filters.deployment_fits, value) })
              }
            >
              {deploymentLabel(value)}
              {count(stats?.by_deployment, value) !== null ? (
                <span className="chip__n">{count(stats?.by_deployment, value)}</span>
              ) : null}
            </button>
          ))}
        </div>
      </div>

      {(stats?.categories ?? []).length > 0 ? (
        <div className="fgroup">
          <h3>Capability</h3>
          <div className="chips">
            {(stats?.categories ?? []).map((bucket) => (
              <button
                key={bucket.key}
                type="button"
                className={`chip${filters.categories.includes(bucket.key) ? ' is-on' : ''}`}
                aria-pressed={filters.categories.includes(bucket.key)}
                onClick={() => onChange({ categories: toggle(filters.categories, bucket.key) })}
              >
                {bucket.label ?? bucket.key.replace(/_/g, ' ')}
                {count(stats?.by_category, bucket.key) !== null ? (
                  <span className="chip__n">{count(stats?.by_category, bucket.key)}</span>
                ) : null}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="fgroup">
        <h3>Source</h3>
        <div className="fgroup__scroll">
          <div className="checks">
            {sources.map((source) => {
              const off = Boolean(source.unavailable_reason);
              return (
                <label className={`check${off ? ' is-off' : ''}`} key={source.name}>
                  <input
                    type="checkbox"
                    disabled={off}
                    checked={filters.sources.includes(source.name)}
                    onChange={() => onChange({ sources: toggle(filters.sources, source.name) })}
                  />
                  {source.display_name}
                  <span className="check__n">
                    {(count(stats?.by_source, source.name) ?? 0).toLocaleString('en-GB')}
                  </span>
                </label>
              );
            })}
          </div>
        </div>
      </div>

      {(stats?.countries ?? []).length > 0 ? (
        <div className="fgroup">
          <h3>Country</h3>
          <div className="fgroup__scroll">
            <div className="checks">
              {(stats?.countries ?? []).map((country) => (
                <label className="check" key={country}>
                  <input
                    type="checkbox"
                    checked={filters.countries.includes(country)}
                    onChange={() => onChange({ countries: toggle(filters.countries, country) })}
                  />
                  {country}
                </label>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      <div className="fgroup">
        <h3>Deadline</h3>
        <span className="sublabel">From</span>
        <input
          className="field"
          type="date"
          value={filters.deadline_from}
          aria-label="Deadline from"
          onChange={(event) => onChange({ deadline_from: event.target.value })}
        />
        <span className="sublabel sublabel--gap">To</span>
        <input
          className="field"
          type="date"
          value={filters.deadline_to}
          aria-label="Deadline to"
          onChange={(event) => onChange({ deadline_to: event.target.value })}
        />
      </div>

      <div className="filters__foot">
        <span className="muted num">{total.toLocaleString('en-GB')} matching</span>
        <span className="toolbar__spacer" />
        <button type="button" className="btn btn--quiet" onClick={onReset} disabled={isDefault}>
          Reset
        </button>
        <button type="button" className="btn" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}
