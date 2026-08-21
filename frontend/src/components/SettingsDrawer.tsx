import type {
  CountBucket,
  DeploymentFit,
  Density,
  FitStatus,
  Preferences,
  SourceStatus,
  Stats,
  TenderFilters,
  Theme,
} from '../types';
import { DEFAULT_FILTERS, DEPLOYMENT_FITS, FIT_STATUSES, PAGE_SIZES } from '../state/urlFilters';
import { deploymentLabel, fitLabel } from '../labels';
import { Drawer } from './Drawer';
import { Icon } from './Icon';

/**
 * Every filter, grouped, in a right-hand drawer (delta 4) - plus the display
 * preferences that used to be scattered across the results header (delta 5).
 *
 * Counts come from /api/stats so a group shows what selecting it would find.
 * Changes apply immediately; there is no Apply button to forget to press.
 */
export interface SettingsDrawerProps {
  open: boolean;
  onClose: () => void;
  filters: TenderFilters;
  stats: Stats | null;
  sources: SourceStatus[];
  preferences: Preferences;
  total: number;
  onChange: (patch: Partial<TenderFilters>) => void;
  onPreferences: (patch: Partial<Preferences>) => void;
  onReset: () => void;
}

const PRESETS: { label: string; patch: Partial<TenderFilters> }[] = [
  {
    label: 'Worth bidding',
    patch: {
      minimum_score: 50,
      maximum_score: 100,
      fit_statuses: [],
      deployment_fits: [],
      active_only: true,
      sort: 'score_desc',
    },
  },
  {
    label: 'Closing this week',
    patch: {
      minimum_score: 40,
      active_only: true,
      has_deadline: true,
      deadline_from: new Date().toISOString().slice(0, 10),
      deadline_to: new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 10),
      sort: 'deadline_asc',
    },
  },
  {
    label: 'Needs review',
    patch: {
      fit_statuses: ['manual_review'],
      minimum_score: 0,
      active_only: true,
      sort: 'score_desc',
    },
  },
  {
    label: 'Excellent fit only',
    patch: { fit_statuses: ['high_fit'], minimum_score: 0, active_only: true, sort: 'score_desc' },
  },
  {
    label: 'Everything (audit)',
    patch: {
      minimum_score: 0,
      maximum_score: 100,
      fit_statuses: [],
      deployment_fits: [],
      active_only: false,
      has_deadline: null,
      sort: 'first_seen_desc',
    },
  },
];

function toggle<T extends string>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function count(buckets: CountBucket[] | undefined, key: string): number | null {
  const found = buckets?.find((b) => b.key === key);
  return found ? found.count : null;
}

function Num({ value }: { value: number | null }) {
  if (value === null) return null;
  return <span className="chip__n">{value.toLocaleString('en-GB')}</span>;
}

function matchesPreset(filters: TenderFilters, patch: Partial<TenderFilters>): boolean {
  return (Object.keys(patch) as (keyof TenderFilters)[]).every((key) => {
    const wanted = patch[key];
    const actual = filters[key];
    if (Array.isArray(wanted) && Array.isArray(actual)) {
      return (
        wanted.length === actual.length &&
        wanted.every((v) => (actual as string[]).includes(v as string))
      );
    }
    return wanted === actual;
  });
}

export function SettingsDrawer({
  open,
  onClose,
  filters,
  stats,
  sources,
  preferences,
  total,
  onChange,
  onPreferences,
  onReset,
}: SettingsDrawerProps) {
  const fitCounts = stats?.by_fit_status;
  const deployCounts = stats?.by_deployment;
  const categoryBuckets = stats?.by_category ?? [];
  const sourceCounts = stats?.by_source;

  return (
    <Drawer open={open} onClose={onClose} label="Filters and settings">
      <header className="drawer__head">
        <div>
          <h2>Filters &amp; settings</h2>
          <p>Changes apply immediately</p>
        </div>
        <span className="spacer" />
        <button
          type="button"
          className="btn btn--icon"
          onClick={onClose}
          aria-label="Close settings"
        >
          <Icon name="close" size={16} />
        </button>
      </header>

      <div className="drawer__body">
        <div className="presets">
          {PRESETS.map((preset) => (
            <button
              type="button"
              key={preset.label}
              className={`preset${matchesPreset(filters, preset.patch) ? ' is-on' : ''}`}
              onClick={() => onChange(preset.patch)}
            >
              {preset.label}
            </button>
          ))}
        </div>

        <section className="section">
          <h3>Relevance score</h3>
          <div className="rangepair">
            <div>
              <div className="fieldlabel">
                <span>Minimum</span>
                <b className="num">{filters.minimum_score}</b>
              </div>
              <input
                className="range"
                type="range"
                min={0}
                max={100}
                step={5}
                value={filters.minimum_score}
                aria-label="Minimum relevance score"
                onChange={(event) => {
                  const value = Number(event.target.value);
                  onChange({
                    minimum_score: value,
                    maximum_score: Math.max(value, filters.maximum_score),
                  });
                }}
              />
            </div>
            <div>
              <div className="fieldlabel">
                <span>Maximum</span>
                <b className="num">{filters.maximum_score}</b>
              </div>
              <input
                className="range"
                type="range"
                min={0}
                max={100}
                step={5}
                value={filters.maximum_score}
                aria-label="Maximum relevance score"
                onChange={(event) => {
                  const value = Number(event.target.value);
                  onChange({
                    maximum_score: value,
                    minimum_score: Math.min(value, filters.minimum_score),
                  });
                }}
              />
            </div>
            <div className="ticks">
              <span>0 not relevant</span>
              <span>50 possible</span>
              <span>85 excellent</span>
            </div>
          </div>
        </section>

        <section className="section">
          <h3>Fit status</h3>
          <div className="chips">
            {FIT_STATUSES.map((value) => (
              <button
                type="button"
                key={value}
                className={`chip${filters.fit_statuses.includes(value) ? ' is-on' : ''}`}
                aria-pressed={filters.fit_statuses.includes(value)}
                onClick={() =>
                  onChange({ fit_statuses: toggle<FitStatus>(filters.fit_statuses, value) })
                }
              >
                {fitLabel(value)}
                <Num value={count(fitCounts, value)} />
              </button>
            ))}
          </div>
        </section>

        <section className="section">
          <h3>
            Deployment fit
            {filters.deployment_fits.length ? (
              <span className="badge badge--green">{filters.deployment_fits.length} selected</span>
            ) : null}
          </h3>
          <div className="chips">
            {DEPLOYMENT_FITS.map((value) => (
              <button
                type="button"
                key={value}
                className={`chip${filters.deployment_fits.includes(value) ? ' is-on' : ''}`}
                aria-pressed={filters.deployment_fits.includes(value)}
                onClick={() =>
                  onChange({
                    deployment_fits: toggle<DeploymentFit>(filters.deployment_fits, value),
                  })
                }
              >
                {deploymentLabel(value)}
                <Num value={count(deployCounts, value)} />
              </button>
            ))}
          </div>
        </section>

        <section className="section">
          <h3>Capability</h3>
          <div className="chips">
            {(stats?.categories ?? []).map((bucket) => (
              <button
                type="button"
                key={bucket.key}
                className={`chip${filters.categories.includes(bucket.key) ? ' is-on' : ''}`}
                aria-pressed={filters.categories.includes(bucket.key)}
                onClick={() => onChange({ categories: toggle(filters.categories, bucket.key) })}
              >
                {bucket.label ?? bucket.key.replace(/_/g, ' ')}
                <Num value={count(categoryBuckets, bucket.key)} />
              </button>
            ))}
          </div>
        </section>

        <section className="section">
          <h3>Sources</h3>
          <div className="checks">
            {sources.map((source) => {
              const unavailable = Boolean(source.unavailable_reason);
              return (
                <label className={`check${unavailable ? ' is-off' : ''}`} key={source.name}>
                  <input
                    type="checkbox"
                    disabled={unavailable}
                    checked={filters.sources.includes(source.name)}
                    onChange={() => onChange({ sources: toggle(filters.sources, source.name) })}
                  />
                  <span>
                    {source.display_name}
                    {unavailable ? (
                      <small className="check__sub">
                        unavailable — {source.unavailable_reason}
                      </small>
                    ) : null}
                  </span>
                  <span className="check__n">
                    {(count(sourceCounts, source.name) ?? 0).toLocaleString('en-GB')}
                  </span>
                </label>
              );
            })}
          </div>
        </section>

        <section className="section">
          <h3>Country &amp; status</h3>
          <div className="grid2">
            <div>
              <span className="sublabel">Country</span>
              <div className="checks">
                {(stats?.countries ?? []).map((country) => (
                  <label className="check" key={country}>
                    <input
                      type="checkbox"
                      checked={filters.countries.includes(country)}
                      onChange={() => onChange({ countries: toggle(filters.countries, country) })}
                    />
                    <span>{country}</span>
                  </label>
                ))}
                {(stats?.countries ?? []).length === 0 ? (
                  <p className="muted">No data yet.</p>
                ) : null}
              </div>
            </div>
            <div>
              <span className="sublabel">Notice status</span>
              <div className="checks">
                {(stats?.statuses ?? []).map((status) => (
                  <label className="check" key={status}>
                    <input
                      type="checkbox"
                      checked={filters.statuses.includes(status)}
                      onChange={() => onChange({ statuses: toggle(filters.statuses, status) })}
                    />
                    <span>{status}</span>
                  </label>
                ))}
                {(stats?.statuses ?? []).length === 0 ? (
                  <p className="muted">No data yet.</p>
                ) : null}
              </div>
            </div>
          </div>
        </section>

        <section className="section">
          <h3>Dates</h3>
          <div className="grid2" style={{ marginBottom: 12 }}>
            <div>
              <span className="sublabel">Deadline from</span>
              <input
                className="input"
                type="date"
                value={filters.deadline_from}
                onChange={(event) => onChange({ deadline_from: event.target.value })}
              />
            </div>
            <div>
              <span className="sublabel">Deadline to</span>
              <input
                className="input"
                type="date"
                value={filters.deadline_to}
                onChange={(event) => onChange({ deadline_to: event.target.value })}
              />
            </div>
            <div>
              <span className="sublabel">Published from</span>
              <input
                className="input"
                type="date"
                value={filters.published_from}
                onChange={(event) => onChange({ published_from: event.target.value })}
              />
            </div>
            <div>
              <span className="sublabel">Published to</span>
              <input
                className="input"
                type="date"
                value={filters.published_to}
                onChange={(event) => onChange({ published_to: event.target.value })}
              />
            </div>
          </div>

          <div className="switchrow">
            <p>
              Open opportunities only
              <small>Hides expired notices and anything already awarded or cancelled</small>
            </p>
            <label className="switch">
              <input
                type="checkbox"
                checked={filters.active_only}
                aria-label="Open opportunities only"
                onChange={(event) => onChange({ active_only: event.target.checked })}
              />
              <span />
            </label>
          </div>

          <div className="switchrow">
            <p>
              Only tenders with a deadline
              <small>AusTender and some feeds publish none</small>
            </p>
            <label className="switch">
              <input
                type="checkbox"
                checked={filters.has_deadline === true}
                aria-label="Only tenders with a deadline"
                onChange={(event) => onChange({ has_deadline: event.target.checked ? true : null })}
              />
              <span />
            </label>
          </div>
        </section>

        <section className="section">
          <h3>Display</h3>
          <div className="switchrow">
            <p>Results per page</p>
            <div className="seg" role="group" aria-label="Results per page">
              {PAGE_SIZES.map((size) => (
                <button
                  type="button"
                  key={size}
                  className={filters.page_size === size ? 'is-on' : ''}
                  aria-pressed={filters.page_size === size}
                  onClick={() => onChange({ page_size: size })}
                >
                  {size}
                </button>
              ))}
            </div>
          </div>
          <div className="switchrow">
            <p>Card density</p>
            <div className="seg" role="group" aria-label="Card density">
              {(['comfortable', 'compact'] as Density[]).map((density) => (
                <button
                  type="button"
                  key={density}
                  className={preferences.density === density ? 'is-on' : ''}
                  aria-pressed={preferences.density === density}
                  onClick={() => onPreferences({ density })}
                >
                  {density === 'comfortable' ? 'Comfortable' : 'Compact'}
                </button>
              ))}
            </div>
          </div>
          <div className="switchrow">
            <p>Theme</p>
            <div className="seg" role="group" aria-label="Theme">
              {(['light', 'dark', 'system'] as Theme[]).map((theme) => (
                <button
                  type="button"
                  key={theme}
                  className={preferences.theme === theme ? 'is-on' : ''}
                  aria-pressed={preferences.theme === theme}
                  onClick={() => onPreferences({ theme })}
                >
                  {theme === 'light' ? 'Light' : theme === 'dark' ? 'Dark' : 'System'}
                </button>
              ))}
            </div>
          </div>
        </section>
      </div>

      <footer className="drawer__foot">
        <span className="count">
          <b className="num">{total.toLocaleString('en-GB')}</b> tenders match
        </span>
        <button
          type="button"
          className="btn"
          onClick={onReset}
          disabled={JSON.stringify(filters) === JSON.stringify(DEFAULT_FILTERS)}
        >
          Reset all
        </button>
        <button type="button" className="btn btn--primary" onClick={onClose}>
          Done
        </button>
      </footer>
    </Drawer>
  );
}
