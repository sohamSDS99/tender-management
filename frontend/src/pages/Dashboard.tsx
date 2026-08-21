import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from '../api/client';
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
  filtersFromSearch,
  searchFromFilters,
} from '../state/urlFilters';
import { resolveTheme, usePreferences } from '../state/preferences';
import { deploymentLabel, fitLabel, pluralise } from '../labels';
import { AutomationNote } from '../components/AutomationNote';
import { DetailDrawer } from '../components/DetailDrawer';
import { Pager } from '../components/Pager';
import { RunsTable } from '../components/RunsTable';
import { SettingsDrawer } from '../components/SettingsDrawer';
import { SourceStrip } from '../components/SourceStrip';
import { StatTiles } from '../components/StatTiles';
import { TenderList } from '../components/TenderList';
import { Toolbar } from '../components/Toolbar';
import { TopBar } from '../components/TopBar';

/**
 * The whole filter set lives in the URL (delta 10), so a view is shareable and
 * survives a refresh - and so the Slack digest can link straight to a filtered
 * dashboard, not just to `?tender=<id>`.
 *
 * There is no control anywhere on this page that starts a fetch. The sweep runs
 * at 00:00 and 12:00 Asia/Dhaka; the header reports it.
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const { preferences, update: setPreferences, toggleTheme } = usePreferences();

  const requestId = useRef(0);

  // --- URL <-> state ------------------------------------------------------
  useEffect(() => {
    const search = searchFromFilters(filters, selectedId);
    const next = `${window.location.pathname}${search ? `?${search}` : ''}`;
    if (next !== `${window.location.pathname}${window.location.search}`) {
      window.history.replaceState(null, '', next);
    }
  }, [filters, selectedId]);

  // Back / forward must restore the view the reader expects.
  useEffect(() => {
    const onPop = () => {
      const parsed = filtersFromSearch(window.location.search);
      setFilters(parsed.filters);
      setSelectedId(parsed.tenderId);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  // Typing debounces; every other change applies at once.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(filters), filters.query ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [filters]);

  // --- data ---------------------------------------------------------------
  const loadTenders = useCallback(async (current: TenderFilters) => {
    const id = ++requestId.current;
    setLoading(true);
    try {
      const result = await api.tenders(current);
      if (id !== requestId.current) return; // a newer request already won
      setPage(result);
      setError(null);
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

  // --- filter helpers -----------------------------------------------------
  const onChange = useCallback((patch: Partial<TenderFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch, page: patch.page ?? 1 }));
  }, []);

  const clearAll = useCallback(() => setFilters(DEFAULT_FILTERS), []);

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

  const goRelative = useCallback(
    (delta: number) => {
      if (selectedIndex < 0) return;
      const next = items[selectedIndex + delta];
      if (next) setSelectedId(next.id);
    },
    [items, selectedIndex],
  );

  const failed = useMemo(
    () => sources.filter((s) => s.unavailable_reason || s.last_status === 'failed'),
    [sources],
  );
  const failedSummary = failed.length
    ? failed.map((s) => `${s.display_name} — ${s.unavailable_reason ?? 'last run failed'}`)[0]
    : 'Every connector reporting healthy';

  const lastRunStartedAt = automation?.last_run?.started_at ?? null;
  // 'system' has to be resolved here or the toggle shows the wrong icon.
  const resolvedTheme = resolveTheme(preferences.theme);

  return (
    <>
      <div className="shell">
        <TopBar automation={automation} onToggleTheme={toggleTheme} theme={resolvedTheme} />

        <AutomationNote automation={automation} />

        <StatTiles
          stats={stats}
          filters={filters}
          onApply={onChange}
          onShowSources={() => setSourcesOpen(true)}
          failedSources={failed.length}
          failedSummary={failedSummary}
        />

        <SourceStrip
          sources={sources}
          open={sourcesOpen}
          onToggle={() => setSourcesOpen((v) => !v)}
          loading={loading}
        />

        <Toolbar
          filters={filters}
          chips={chips}
          activeCount={chips.length}
          onChange={onChange}
          onOpenSettings={() => setSettingsOpen(true)}
          onClearAll={clearAll}
        />

        <main>
          <div className="results__head">
            <h2 aria-live="polite">
              {page
                ? `${page.total.toLocaleString('en-GB')} matching ${pluralise(page.total, 'tender')}`
                : 'Tenders'}
              {page && page.pages > 1 ? (
                <span className="muted">
                  {' '}
                  · page {page.page} of {page.pages}
                </span>
              ) : null}
            </h2>
          </div>

          <TenderList
            tenders={items}
            loading={loading}
            error={error}
            selectedId={selectedId}
            newSince={lastRunStartedAt}
            activeFilterCount={chips.length}
            onSelect={setSelectedId}
            onRetry={() => setReloadToken((v) => v + 1)}
            onClearFilters={clearAll}
            onLowerScore={() => onChange({ minimum_score: 25 })}
          />

          {page && !loading && !error ? (
            <Pager page={page} onGo={(next) => setFilters((prev) => ({ ...prev, page: next }))} />
          ) : null}

          <RunsTable runs={runs} />
        </main>
      </div>

      <div
        className={`scrim${settingsOpen || selectedId !== null ? ' is-on' : ''}`}
        onClick={() => {
          if (selectedId !== null) setSelectedId(null);
          else setSettingsOpen(false);
        }}
      />

      <SettingsDrawer
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        filters={filters}
        stats={stats}
        sources={sources}
        preferences={preferences}
        total={page?.total ?? 0}
        onChange={onChange}
        onPreferences={setPreferences}
        onReset={clearAll}
      />

      <DetailDrawer
        tenderId={selectedId}
        position={position}
        onClose={() => setSelectedId(null)}
        onPrev={() => goRelative(-1)}
        onNext={() => goRelative(1)}
        hasPrev={selectedIndex > 0}
        hasNext={selectedIndex >= 0 && selectedIndex < items.length - 1}
      />
    </>
  );
}
