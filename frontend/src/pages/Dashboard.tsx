import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { FetchRun, SourceStatus, Stats, TenderFilters, TenderPage } from '../types';
import { formatDateTime } from '../labels';
import { FilterPanel } from '../components/FilterPanel';
import { SourceCards } from '../components/SourceCards';
import { StatsBar } from '../components/StatsBar';
import { TenderList } from '../components/TenderList';
import { TenderPanel } from '../components/TenderPanel';

// Default view: active opportunities scoring 50 or higher.
const DEFAULT_FILTERS: TenderFilters = {
  query: '',
  minimum_score: 50,
  sources: [],
  countries: [],
  categories: [],
  statuses: [],
  fit_statuses: [],
  deployment_fits: [],
  deadline_to: '',
  active_only: true,
  sort: 'score_desc',
  page: 1,
  page_size: 25,
};

export function Dashboard() {
  const [filters, setFilters] = useState<TenderFilters>(DEFAULT_FILTERS);
  const [debounced, setDebounced] = useState<TenderFilters>(DEFAULT_FILTERS);
  const [page, setPage] = useState<TenderPage | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [sources, setSources] = useState<SourceStatus[]>([]);
  const [runs, setRuns] = useState<FetchRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);
  // The open tender lives in the URL (?tender=<id>) so a detail view is shareable.
  const [selectedId, setSelectedId] = useState<number | null>(() => {
    const raw = new URLSearchParams(window.location.search).get('tender');
    return raw && /^\d+$/.test(raw) ? Number(raw) : null;
  });
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(filters), filters.query ? 350 : 0);
    return () => window.clearTimeout(timer);
  }, [filters]);

  const loadTenders = useCallback(async (current: TenderFilters) => {
    setLoading(true);
    try {
      setPage(await api.tenders(current));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMeta = useCallback(async () => {
    try {
      const [statsData, sourcesData, runsData] = await Promise.all([
        api.stats(),
        api.sources(),
        api.fetchRuns(12),
      ]);
      setStats(statsData);
      setSources(sourcesData);
      setRuns(runsData);
      return sourcesData;
    } catch {
      return [];
    }
  }, []);

  useEffect(() => {
    void loadTenders(debounced);
  }, [debounced, loadTenders]);

  useEffect(() => {
    void loadMeta();
  }, [loadMeta]);

  // While a fetch is running, poll until every source has finished.
  useEffect(() => {
    if (!fetching) return;
    const tick = async () => {
      const current = await loadMeta();
      if (current.length > 0 && !current.some((source) => source.running)) {
        setFetching(false);
        void loadTenders(debounced);
      }
    };
    pollRef.current = window.setInterval(tick, 3000);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [fetching, loadMeta, loadTenders, debounced]);

  const select = useCallback((id: number | null) => {
    setSelectedId(id);
    const url = new URL(window.location.href);
    if (id === null) url.searchParams.delete('tender');
    else url.searchParams.set('tender', String(id));
    window.history.replaceState(null, '', url);
  }, []);

  const onChange = (patch: Partial<TenderFilters>) =>
    setFilters((prev) => ({ ...prev, ...patch, page: patch.page ?? 1 }));

  const startFetch = async () => {
    setNotice(null);
    try {
      const response = await api.startFetch();
      setFetching(true);
      const skipped = response.skipped_sources.length
        ? ` (already running: ${response.skipped_sources.join(', ')})`
        : '';
      setNotice(`Fetching ${response.runs.length} source(s)${skipped}…`);
      void loadMeta();
    } catch (err) {
      setNotice(err instanceof ApiError ? `Fetch failed: ${err.message}` : String(err));
    }
  };

  const rescore = async () => {
    setNotice(null);
    try {
      const { rescored } = await api.rescore();
      setNotice(`Re-scored ${rescored} tenders with the current configuration.`);
      void loadMeta();
      void loadTenders(debounced);
    } catch (err) {
      setNotice(err instanceof ApiError ? `Re-score failed: ${err.message}` : String(err));
    }
  };

  const lastRuns = useMemo(() => runs.slice(0, 6), [runs]);

  return (
    <div className="shell">
      <header className="topbar">
        <div>
          <h1>Tender Monitor</h1>
          <p>SDS management · SDS authoring · chemical compliance · EHS software opportunities</p>
        </div>
        <div className="topbar__actions">
          <span className="topbar__meta">
            Last successful fetch: {formatDateTime(stats?.last_successful_fetch)}
          </span>
          <button className="button" onClick={rescore}>
            Re-score
          </button>
          <button className="button button--primary" onClick={startFetch} disabled={fetching}>
            {fetching ? 'Fetching…' : 'Fetch new tenders'}
          </button>
        </div>
      </header>

      {notice && <p className="notice">{notice}</p>}

      <StatsBar stats={stats} loading={loading} />
      <SourceCards sources={sources} loading={loading && sources.length === 0} />

      <div className="layout">
        <FilterPanel
          filters={filters}
          stats={stats}
          sources={sources}
          onChange={onChange}
          onReset={() => setFilters(DEFAULT_FILTERS)}
        />

        <main className="results">
          <div className="results__head">
            <h2>
              {page ? `${page.total} matching tender${page.total === 1 ? '' : 's'}` : 'Tenders'}
            </h2>
            <label className="field field--inline">
              <span>Per page</span>
              <select
                value={filters.page_size}
                onChange={(event) => onChange({ page_size: Number(event.target.value) })}
              >
                {[10, 25, 50, 100].map((size) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <TenderList
            tenders={page?.items ?? []}
            loading={loading}
            error={error}
            selectedId={selectedId}
            onSelect={select}
          />

          {page && page.pages > 1 && (
            <nav className="pager" aria-label="Pagination">
              <button
                className="button"
                disabled={page.page <= 1}
                onClick={() => setFilters((prev) => ({ ...prev, page: prev.page - 1 }))}
              >
                ← Previous
              </button>
              <span>
                Page {page.page} of {page.pages}
              </span>
              <button
                className="button"
                disabled={page.page >= page.pages}
                onClick={() => setFilters((prev) => ({ ...prev, page: prev.page + 1 }))}
              >
                Next →
              </button>
            </nav>
          )}

          {lastRuns.length > 0 && (
            <details className="runs">
              <summary>Recent fetch runs</summary>
              <table>
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Status</th>
                    <th>Received</th>
                    <th>New</th>
                    <th>Updated</th>
                    <th>Started</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {lastRuns.map((run) => (
                    <tr key={run.id}>
                      <td className="mono">{run.source}</td>
                      <td className={`run run--${run.status}`}>{run.status}</td>
                      <td>{run.records_received}</td>
                      <td>{run.records_created}</td>
                      <td>{run.records_updated}</td>
                      <td>{formatDateTime(run.started_at)}</td>
                      <td className="run__error">{run.error_message?.slice(0, 160) ?? ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </main>
      </div>

      <TenderPanel tenderId={selectedId} onClose={() => select(null)} />
    </div>
  );
}
