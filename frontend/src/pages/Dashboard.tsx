import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api } from '../api/client';
import type {
  AutomationStatus,
  FetchRun,
  SourceStatus,
  Stats,
  TenderFilters,
  TenderPage,
} from '../types';
import {
  DEFAULT_FILTERS,
  activeChips,
  correctedPage,
  filtersFromSearch,
  searchFromFilters,
} from '../state/urlFilters';
import { OWNED, VIEWS, activeView, type ViewContext, type ViewKey } from '../state/views';
import { resolveTheme, usePreferences } from '../state/preferences';
import { deploymentLabel, fitLabel } from '../labels';
import { DetailPanel } from '../components/DetailPanel';
import { Filters } from '../components/Filters';
import { Masthead } from '../components/Masthead';
import { Notice } from '../components/Notice';
import { Pager } from '../components/Pager';
import { SystemSection } from '../components/SystemSection';
import { TenderList } from '../components/TenderList';
import { Toolbar } from '../components/Toolbar';
import { Views } from '../components/Views';
import { Icon } from '../components/Icon';

/**
 * The whole filter set lives in the URL, so any view is shareable and survives a
 * refresh — and so a Slack digest can link to a filtered dashboard, not only to
 * `?tender=<id>`.
 *
 * Nothing here can start a fetch. Sweeps run at 00:00 and 12:00 Asia/Dhaka; the
 * masthead reports them.
 */
const initial = filtersFromSearch(window.location.search);

export function Dashboard() {
  const [filters, setFilters] = useState<TenderFilters>(initial.filters);
  const [debounced, setDebounced] = useState<TenderFilters>(initial.filters);
  const [selectedId, setSelectedId] = useState<number | null>(initial.tenderId);

  const [page, setPage] = useState<TenderPage | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [sources, setSources] = useState<SourceStatus[]>([]);
  const [runs, setRuns] = useState<FetchRun[]>([]);
  const [automation, setAutomation] = useState<AutomationStatus | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const { preferences, toggleTheme } = usePreferences();
  const requestId = useRef(0);

  // --- URL <-> state ------------------------------------------------------
  useEffect(() => {
    const search = searchFromFilters(filters, selectedId);
    const next = `${window.location.pathname}${search ? `?${search}` : ''}`;
    if (next !== `${window.location.pathname}${window.location.search}`) {
      window.history.replaceState(null, '', next);
    }
  }, [filters, selectedId]);

  useEffect(() => {
    const onPop = () => {
      const parsed = filtersFromSearch(window.location.search);
      setFilters(parsed.filters);
      setSelectedId(parsed.tenderId);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(filters), filters.query ? 280 : 0);
    return () => window.clearTimeout(timer);
  }, [filters]);

  // --- data ---------------------------------------------------------------
  const loadTenders = useCallback(async (current: TenderFilters) => {
    const id = ++requestId.current;
    setLoading(true);
    try {
      const result = await api.tenders(current);
      if (id !== requestId.current) return;
      setPage(result);
      setError(null);
      // A shared or stale link can name a page past the end of the result set —
      // which showed zero rows under a count that said there were six, with no
      // pager to escape by. Correct the URL instead of stranding the reader.
      const corrected = correctedPage(result.page, result.pages);
      if (corrected !== null) {
        setFilters((prev) => (prev.page === corrected ? prev : { ...prev, page: corrected }));
      }
    } catch (err) {
      if (id !== requestId.current) return;
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, []);

  const loadMeta = useCallback(async () => {
    const results = await Promise.allSettled([
      api.stats(),
      api.sources(),
      api.fetchRuns(16),
      api.automation(),
    ]);
    if (results[0].status === 'fulfilled') setStats(results[0].value);
    if (results[1].status === 'fulfilled') setSources(results[1].value);
    if (results[2].status === 'fulfilled') setRuns(results[2].value);
    if (results[3].status === 'fulfilled') setAutomation(results[3].value);
  }, []);

  useEffect(() => {
    void loadTenders(debounced);
  }, [debounced, loadTenders, reloadToken]);

  useEffect(() => {
    void loadMeta();
  }, [loadMeta, reloadToken]);

  // --- filters and views --------------------------------------------------
  const onChange = useCallback((patch: Partial<TenderFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch, page: patch.page ?? 1 }));
  }, []);

  const clearAll = useCallback(() => setFilters(DEFAULT_FILTERS), []);

  const viewContext: ViewContext = useMemo(
    () => ({
      lastRunAt: automation?.last_run?.started_at ?? null,
      goodFitBand: stats?.score_bands?.good_fit ?? 70,
    }),
    [automation, stats],
  );

  const selectView = useCallback(
    (key: ViewKey) => {
      const view = VIEWS.find((v) => v.key === key);
      if (!view) return;
      setFilters({ ...DEFAULT_FILTERS, ...view.patch(viewContext), page: 1 });
    },
    [viewContext],
  );

  const chips = useMemo(
    () =>
      activeChips(filters, {
        fit: fitLabel,
        deployment: deploymentLabel,
        source: (name) => sources.find((s) => s.name === name)?.display_name ?? name,
        category: (key) =>
          stats?.categories.find((c) => c.key === key)?.label ?? key.replace(/_/g, ' '),
      }),
    [filters, sources, stats],
  );

  // --- detail navigation --------------------------------------------------
  const items = page?.items ?? [];
  const selectedIndex = items.findIndex((t) => t.id === selectedId);
  const position =
    selectedIndex >= 0 && page
      ? { index: (page.page - 1) * page.page_size + selectedIndex + 1, total: page.total }
      : null;

  const step = useCallback(
    (delta: number) => {
      if (selectedIndex < 0) return;
      const next = items[selectedIndex + delta];
      if (next) setSelectedId(next.id);
    },
    [items, selectedIndex],
  );

  const theme = resolveTheme(preferences.theme);
  // While a view tab is lit it already says what is being filtered, so neither
  // the chip row nor the Filters badge should repeat it. What they must still
  // show is anything the user narrowed *beyond* the view — a source, a country —
  // because that is invisible otherwise.
  const currentView = activeView(filters, viewContext);
  const extraChips = useMemo(
    () => (currentView ? chips.filter((chip) => !OWNED.includes(chip.key as never)) : chips),
    [chips, currentView],
  );

  return (
    <>
      <div className="page">
        <Masthead automation={automation} theme={theme} onToggleTheme={toggleTheme} />

        <Views filters={filters} stats={stats} context={viewContext} onSelect={selectView} />

        <Toolbar
          filters={filters}
          chips={extraChips}
          filtersOpen={filtersOpen}
          onChange={onChange}
          onToggleFilters={() => setFiltersOpen((open) => !open)}
        />

        {filtersOpen ? (
          <Filters
            filters={filters}
            stats={stats}
            sources={sources}
            total={page?.total ?? 0}
            onChange={onChange}
            onReset={clearAll}
            onClose={() => setFiltersOpen(false)}
          />
        ) : extraChips.length > 0 ? (
          <div className="active">
            {extraChips.map((chip) => (
              <span className="fchip" key={chip.key}>
                {chip.label}
                <button
                  type="button"
                  aria-label={`Remove filter: ${chip.label}`}
                  onClick={() => onChange(chip.clear)}
                >
                  <Icon name="close" size={11} />
                </button>
              </span>
            ))}
          </div>
        ) : null}

        <Notice automation={automation} />

        <main>
          <p className="count" aria-live="polite">
            {page
              ? `${page.total.toLocaleString('en-GB')} ${page.total === 1 ? 'tender' : 'tenders'}`
              : ' '}
          </p>

          <TenderList
            tenders={items}
            loading={loading}
            error={error}
            selectedId={selectedId}
            newSince={viewContext.lastRunAt}
            filterCount={extraChips.length}
            total={page?.total ?? 0}
            onSelect={setSelectedId}
            onRetry={() => setReloadToken((v) => v + 1)}
            onClearFilters={clearAll}
            onFirstPage={() => setFilters((prev) => ({ ...prev, page: 1 }))}
          />

          {page && !error ? (
            <Pager page={page} onGo={(next) => setFilters((prev) => ({ ...prev, page: next }))} />
          ) : null}
        </main>

        <SystemSection automation={automation} sources={sources} runs={runs} />
      </div>

      <div
        className={`scrim${selectedId !== null ? ' is-on' : ''}`}
        onClick={() => setSelectedId(null)}
      />

      <DetailPanel
        tenderId={selectedId}
        position={position}
        onClose={() => setSelectedId(null)}
        onPrev={() => step(-1)}
        onNext={() => step(1)}
        hasPrev={selectedIndex > 0}
        hasNext={selectedIndex >= 0 && selectedIndex < items.length - 1}
      />
    </>
  );
}
