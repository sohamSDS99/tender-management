import type {
  CountBucket,
  DeploymentFit,
  FitStatus,
  SourceStatus,
  Stats,
  TenderFilters,
} from '../types';
import { DEPLOYMENT_FITS, FIT_STATUSES, isDefaultFilters } from '../state/urlFilters';
import { countryLabel, deploymentLabel, fitLabel } from '../labels';
import { day } from '../state/lenses';
import { Icon } from './Icon';

/**
 * Named starting points. Each is a whole filter set, not an additive toggle, so
 * picking one always lands somewhere predictable rather than on top of whatever
 * was already set.
 */
const PRESETS: {
  label: string;
  patch: (bands: { good: number; possible: number }) => Partial<TenderFilters>;
}[] = [
  {
    label: 'Worth bidding',
    patch: (b) => ({
      minimum_score: b.possible,
      maximum_score: 100,
      active_only: true,
      sort: 'score_desc',
    }),
  },
  {
    label: 'Closing this week',
    patch: () => ({
      minimum_score: 0,
      active_only: true,
      deadline_from: day(0),
      deadline_to: day(7),
      sort: 'deadline_asc',
    }),
  },
  {
    label: 'Needs review',
    patch: () => ({ minimum_score: 0, active_only: true, fit_statuses: ['manual_review'] }),
  },
  {
    label: 'Excellent fit only',
    patch: () => ({ minimum_score: 0, active_only: true, fit_statuses: ['high_fit'] }),
  },
  {
    label: 'Everything (audit)',
    patch: () => ({
      minimum_score: 0,
      maximum_score: 100,
      active_only: false,
      fit_statuses: [],
      deployment_fits: [],
      sort: 'first_seen_desc',
    }),
  },
];

/**
 * The filters, and only the filters.
 *
 * Display and Automation moved out to their own settings pages; what is left is
 * the one group that has to stay here. The panel carries no blocking scrim on
 * purpose: it covers the left of the page, but the results stay visible and
 * clickable to its right, so a filter can be watched taking effect while it is
 * still being set. That is the entire argument for a panel over a page, and it
 * only applies to controls that change what the list contains.
 *
 * Reached from Settings -> Filters in the rail menu. Escape closes it.
 */
export function SettingsPanel({
  open,
  filters,
  stats,
  sources,
  total,
  onChange,
  onReset,
  onClose,
}: {
  open: boolean;
  filters: TenderFilters;
  stats: Stats | null;
  sources: SourceStatus[];
  total: number;
  onChange: (patch: Partial<TenderFilters>) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const bands = {
    good: stats?.score_bands?.good_fit ?? 70,
    possible: stats?.score_bands?.possible_fit ?? 50,
  };

  const storedBySource = (buckets: CountBucket[] | undefined, key: string): number =>
    buckets?.find((b) => b.key === key)?.count ?? 0;

  const toggle = <T extends string>(list: T[], value: T): T[] =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  const isDefault = isDefaultFilters(filters);

  return (
    <aside
      id="settings-slideout"
      className={`slideout${open ? ' is-on' : ''}`}
      aria-label="Settings"
      // The stylesheet also sets visibility:hidden once the close transition has
      // finished, which is what actually removes the shut panel's controls from
      // tab order — a panel translated off-screen still holds its tab stops.
      aria-hidden={!open}
    >
      <header className="panelhead">
        <div>
          <h2>Filters</h2>
          <p>Changes apply immediately, and the results stay live beside this panel</p>
        </div>
        <span className="spacer" />
        <button
          type="button"
          className="btn btn--icon"
          aria-label="Hide settings"
          title="Hide settings"
          onClick={onClose}
        >
          <Icon name="close" size={16} />
        </button>
      </header>

      <div className="slideout__body">
        <div className="presets">
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              className="preset"
              onClick={() => onChange(preset.patch(bands))}
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
                onChange={(event) => onChange({ minimum_score: Number(event.target.value) })}
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
                onChange={(event) => onChange({ maximum_score: Number(event.target.value) })}
              />
            </div>
            <div className="ticks">
              <span>0 not relevant</span>
              <span>{bands.possible} possible</span>
              <span>{bands.good} excellent</span>
            </div>
          </div>
        </section>

        {/* No counts on these three, deliberately. They would come from unfiltered
            /api/stats, so inside a narrowed view they promise results that are not
            there — "Not fit 274" once returned zero rows. Source counts below are
            different: those are explicitly stored totals. */}
        <section className="section">
          <h3>Fit status</h3>
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
              </button>
            ))}
          </div>
        </section>

        <section className="section">
          <h3>
            Deployment fit
            {filters.deployment_fits.length > 0 ? (
              <span className="badge badge--green">{filters.deployment_fits.length} selected</span>
            ) : null}
          </h3>
          <div className="chips">
            {DEPLOYMENT_FITS.map((value) => (
              <button
                key={value}
                type="button"
                className={`chip${filters.deployment_fits.includes(value) ? ' is-on' : ''}`}
                aria-pressed={filters.deployment_fits.includes(value)}
                onClick={() =>
                  onChange({
                    deployment_fits: toggle<DeploymentFit>(filters.deployment_fits, value),
                  })
                }
              >
                {deploymentLabel(value)}
              </button>
            ))}
          </div>
        </section>

        {(stats?.categories ?? []).length > 0 ? (
          <section className="section">
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
                </button>
              ))}
            </div>
          </section>
        ) : null}

        <section className="section">
          <h3>Sources</h3>
          <div className="checks">
            {sources.map((source) => {
              const stored = storedBySource(stats?.by_source, source.name);
              // Disabled on "has nothing to show", not on "cannot fetch": a source
              // with no API key can still have stored notices, and greying it out
              // made those unreachable through the filter.
              const off = stored === 0;
              return (
                <label className={`check${off ? ' is-off' : ''}`} key={source.name}>
                  <input
                    type="checkbox"
                    disabled={off}
                    checked={filters.sources.includes(source.name)}
                    onChange={() => onChange({ sources: toggle(filters.sources, source.name) })}
                  />
                  <span>
                    {source.display_name}
                    {source.unavailable_reason ? (
                      <small className="check__sub">{source.unavailable_reason}</small>
                    ) : null}
                  </span>
                  <span className="check__n">{stored.toLocaleString('en-GB')}</span>
                </label>
              );
            })}
          </div>
        </section>

        {(stats?.countries ?? []).length > 0 || (stats?.statuses ?? []).length > 0 ? (
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
                      <span>{countryLabel(country)}</span>
                    </label>
                  ))}
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
                </div>
              </div>
            </div>
          </section>
        ) : null}

        <section className="section">
          <h3>Dates</h3>
          <div className="grid2" style={{ marginBottom: 12 }}>
            <div>
              <span className="sublabel">Deadline from</span>
              <input
                className="input"
                type="date"
                value={filters.deadline_from}
                aria-label="Deadline from"
                onChange={(event) => onChange({ deadline_from: event.target.value })}
              />
            </div>
            <div>
              <span className="sublabel">Deadline to</span>
              <input
                className="input"
                type="date"
                value={filters.deadline_to}
                aria-label="Deadline to"
                onChange={(event) => onChange({ deadline_to: event.target.value })}
              />
            </div>
            <div>
              <span className="sublabel">Published from</span>
              <input
                className="input"
                type="date"
                value={filters.published_from}
                aria-label="Published from"
                onChange={(event) => onChange({ published_from: event.target.value })}
              />
            </div>
            <div>
              <span className="sublabel">Published to</span>
              <input
                className="input"
                type="date"
                value={filters.published_to}
                aria-label="Published to"
                onChange={(event) => onChange({ published_to: event.target.value })}
              />
            </div>
          </div>
          <div className="switchrow">
            <p>
              Open opportunities only
              <small>Hides expired, cancelled and awarded notices</small>
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
              <small>Some feeds publish none at all</small>
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
      </div>

      <footer className="panelfoot">
        <span className="count">
          <b className="num">{total.toLocaleString('en-GB')}</b> match
        </span>
        <button type="button" className="btn btn--sm" onClick={onReset} disabled={isDefault}>
          Reset all
        </button>
      </footer>
    </aside>
  );
}
