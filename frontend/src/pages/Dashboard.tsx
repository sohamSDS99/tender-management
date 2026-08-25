import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api } from '../api/client';
import type {
  AutomationStatus,
  Density,
  FetchRun,
  SourceStatus,
  Stats,
  TenderFilters,
  TenderPage,
} from '../types';
import {
  DEFAULT_FILTERS,
  activeChips,
  activeFilterCount,
  correctedPage,
  filtersFromSearch,
  searchFromFilters,
} from '../state/urlFilters';
import {
  OWNED,
  activeLens,
  lensByKey,
  type LensContext,
  type LensKey,
} from '../state/lenses';
import { usePreferences } from '../state/preferences';
import {
  FALLBACK_BANDS,
  countryLabel,
  deploymentLabel,
  fitLabel,
  makeSourceLabel,
} from '../labels';
import { DetailPanel } from '../components/DetailPanel';
import { LinkBase } from '../components/LinkBase';
import { BucketNote, Notice } from '../components/Notice';
import { Pager } from '../components/Pager';
import { RunsTable } from '../components/RunsTable';
import { SettingsPanel } from '../components/SettingsPanel';
import { SourcesPanel } from '../components/SourcesPanel';
import { TenderList } from '../components/TenderList';
import { MatchingRulesSettings } from '../components/MatchingRulesSettings';
import { Sidebar, type SettingsScreen } from '../components/Sidebar';
import { SourcesSettings } from '../components/SourcesSettings';
import { Toolbar } from '../components/Toolbar';
import { ScheduleEditor } from '../components/ScheduleEditor';
import { TriggerSwitch } from '../components/TriggerSwitch';

/**
 * The whole filter set lives in the URL, so any view is shareable and survives a
 * refresh — and so a Slack digest can link to a filtered dashboard, not only to
 * `?tender=<id>`.
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
  const [unreachable, setUnreachable] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  /** Which settings screen has taken over the content area, if any. */
  const [settingsScreen, setSettingsScreen] = useState<SettingsScreen | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  // Which whole-system action is in flight, and what the server said about it.
  const [busy, setBusy] = useState<'fetch' | 'rescore' | null>(null);
  const [busySource, setBusySource] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(
    null,
  );

  const { preferences, update } = usePreferences();
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
      setUnreachable(false);
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
      // Only status 0 means the API could not be reached; anything else is a
      // rejected request or a fault in this app, and blaming the backend for it
      // sends the reader after the wrong problem.
      setUnreachable(err instanceof ApiError && err.status === 0);
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

  // --- whole-system actions (D23) -----------------------------------------
  /**
   * A sweep runs for about thirteen minutes in the background, so the 202 only
   * means "started". Poll the metadata while it runs rather than leaving the page
   * claiming the old numbers.
   */
  const sweeping = automation?.last_run?.status === 'running';
  useEffect(() => {
    if (!sweeping) return;
    const timer = window.setInterval(() => void loadMeta(), 15_000);
    return () => window.clearInterval(timer);
  }, [sweeping, loadMeta]);

  const runAction = useCallback(
    async (kind: 'fetch' | 'rescore', source?: string) => {
      setBusy(kind);
      if (source) setBusySource(source);
      setActionMessage(null);
      try {
        if (kind === 'fetch') {
          const started = await api.fetchNow(source ? [source] : undefined);
          setActionMessage({
            tone: 'ok',
            text: `Sweep started across ${started.run_ids.length} source${started.run_ids.length === 1 ? '' : 's'}. This takes a few minutes; the page updates as it goes.`,
          });
        } else {
          const result = await api.rescore();
          setActionMessage({
            tone: 'ok',
            text: `Re-scored ${result.rescored.toLocaleString('en-GB')} notices against the current profile.`,
          });
          setReloadToken((v) => v + 1);
        }
        await loadMeta();
      } catch (err) {
        // The server's message is written for the person who clicked — a cooldown
        // says how many seconds are left — so show it rather than a generic one.
        setActionMessage({
          tone: 'bad',
          text: err instanceof ApiError ? err.message : String(err),
        });
      } finally {
        setBusy(null);
        setBusySource(null);
      }
    },
    [loadMeta],
  );

  // --- filters and views --------------------------------------------------
  const onChange = useCallback((patch: Partial<TenderFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch, page: patch.page ?? 1 }));
  }, []);

  const clearAll = useCallback(() => setFilters(DEFAULT_FILTERS), []);

  const lensContext: LensContext = useMemo(
    () => ({
      lastRunAt: automation?.last_run?.started_at ?? null,
      goodFitBand: stats?.score_bands?.good_fit ?? FALLBACK_BANDS.good_fit,
      possibleFitBand: stats?.score_bands?.possible_fit ?? FALLBACK_BANDS.possible_fit,
    }),
    [automation, stats],
  );

  // The engine's own bands, so the score colour and the fit badge never disagree.
  const bands = useMemo(
    () => ({
      good_fit: stats?.score_bands?.good_fit ?? FALLBACK_BANDS.good_fit,
      possible_fit: stats?.score_bands?.possible_fit ?? FALLBACK_BANDS.possible_fit,
    }),
    [stats],
  );

  // Machine keys like "world_bank" have no business on screen.
  const sourceLabel = useMemo(
    () => makeSourceLabel(Object.fromEntries(sources.map((s) => [s.name, s.display_name]))),
    [sources],
  );

  const selectLens = useCallback(
    (key: LensKey) => {
      const lens = lensByKey(key);
      if (!lens) return;
      setSettingsScreen(null);
      setFilters({ ...DEFAULT_FILTERS, ...lens.patch(lensContext), page: 1 });
    },
    [lensContext],
  );

  // Machine keys like "sds_management" have no business on screen, on a chip or
  // on a card badge.
  const categoryLabel = useCallback(
    (key: string) => stats?.categories.find((c) => c.key === key)?.label ?? key.replace(/_/g, ' '),
    [stats],
  );

  const chips = useMemo(
    () =>
      activeChips(filters, {
        fit: fitLabel,
        deployment: deploymentLabel,
        source: sourceLabel,
        category: categoryLabel,
        country: countryLabel,
      }),
    [filters, sourceLabel, categoryLabel],
  );

  const currentLens = activeLens(filters, lensContext);

  /**
   * The chip row states the complete truth about what is on screen: the lens's
   * own predicate first, locked, then anything the reader narrowed on top.
   *
   * Locked because the lens is where you *are*. It changes by navigating, not
   * by dismissing a chip — which is also why "Clear all" only clears the rest.
   */
  const chipRow = useMemo(() => {
    const lens = lensByKey(currentLens);
    const locked = lens?.lockedLabel(lensContext) ?? null;
    const refinements = (currentLens ? chips.filter((chip) => !OWNED.includes(chip.key as never)) : chips).map(
      (chip) => ({ label: chip.label, onRemove: () => onChange(chip.clear) }),
    );
    const row: { label: string; locked?: boolean; onRemove: () => void }[] = refinements;
    return locked === null ? row : [{ label: locked, locked: true, onRemove: () => {} }, ...row];
  }, [chips, currentLens, lensContext, onChange]);

  const lensNote = useMemo(() => {
    const lens = lensByKey(currentLens);
    return lens ? lens.note(lensContext) : null;
  }, [currentLens, lensContext]);

  const brokenSources = useMemo(
    () => sources.filter((s) => s.unavailable_reason || s.last_status === 'failed').length,
    [sources],
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

  const settingsOpen = preferences.settingsOpen;

  // Escape closes the slide-out — but not while the detail drawer is up, which
  // owns Escape for itself and is stacked above it.
  useEffect(() => {
    if (!settingsOpen || selectedId !== null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') update({ settingsOpen: false });
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [settingsOpen, selectedId, update]);

  return (
    <>
      <div className="shell">
        <div className="col">
          {settingsScreen === 'sources' ? (
            <SourcesSettings
              sources={sources}
              busySource={busySource}
              onFetchSource={(name) => void runAction('fetch', name)}
              onChanged={() => void loadMeta()}
            />
          ) : settingsScreen === 'rules' ? (
            <MatchingRulesSettings
              onRescored={() => {
                void loadMeta();
                setReloadToken((v) => v + 1);
              }}
            />
          ) : (
          <>
          <SourcesPanel
            sources={sources}
            open={sourcesOpen}
            onToggle={setSourcesOpen}
            lastSweepAt={automation?.last_run?.started_at ?? null}
            busySource={busySource}
            onFetchSource={(name) => void runAction('fetch', name)}
          />

          <Toolbar
            filters={filters}
            filterCount={activeFilterCount(filters)}
            onOpenFilters={() => update({ settingsOpen: true })}
            onSearch={(query) => onChange({ query })}
            onSort={(sort) => onChange({ sort })}
            onClearAll={clearAll}
            chips={chipRow}
          />

          {actionMessage ? (
            <p
              className={`notice${actionMessage.tone === 'bad' ? ' notice--bad' : ' notice--ok'}`}
              role="status"
            >
              {actionMessage.text}
            </p>
          ) : null}

          <Notice automation={automation} />

          <main>
            {lensNote ? <BucketNote text={lensNote} /> : null}

            <div className="results__head">
              <h2 aria-live="polite">
                {/* Suppressed while erroring: the last successful count is stale,
                      and showing "6 tenders" above "cannot reach the API"
                      contradicts itself. */}
                {page && !error ? (
                  <>
                    {page.total.toLocaleString('en-GB')} {page.total === 1 ? 'tender' : 'tenders'}
                    {page.pages > 1 ? (
                      <span className="muted">
                        {' '}
                        · page {page.page} of {page.pages}
                      </span>
                    ) : null}
                  </>
                ) : (
                  ' '
                )}
              </h2>
            </div>

            <TenderList
              tenders={items}
              loading={loading}
              error={error}
              unreachable={unreachable}
              selectedId={selectedId}
              newSince={lensContext.lastRunAt}
              filterCount={activeFilterCount(filters)}
              total={page?.total ?? 0}
              storedTotal={stats?.total_tenders ?? 0}
              bands={bands}
              sourceLabel={sourceLabel}
              categoryLabel={categoryLabel}
              onSelect={setSelectedId}
              onRetry={() => setReloadToken((v) => v + 1)}
              onClearFilters={clearAll}
              onFirstPage={() => setFilters((prev) => ({ ...prev, page: 1 }))}
              onShowAll={() => selectLens('all')}
            />

            {page && !error ? (
              <Pager page={page} onGo={(next) => setFilters((prev) => ({ ...prev, page: next }))} />
            ) : null}
          </main>

          <RunsTable runs={runs} sourceLabel={sourceLabel} />
          </>
          )}
        </div>
      </div>

      <Sidebar
        stats={stats}
        lensContext={lensContext}
        activeLens={currentLens}
        settingsScreen={settingsScreen}
        brokenSources={brokenSources}
        busy={busy}
        sweeping={automation?.last_run?.status === 'running' || busy === 'fetch'}
        onSelectLens={selectLens}
        onOpenSettings={setSettingsScreen}
        onFetch={() => void runAction('fetch')}
        onRescore={() => void runAction('rescore')}
      />

      <SettingsPanel
        open={settingsOpen}
        filters={filters}
        stats={stats}
        sources={sources}
        total={page?.total ?? 0}
        density={preferences.density}
        pageSize={filters.page_size}
        onChange={onChange}
        onReset={clearAll}
        onClose={() => update({ settingsOpen: false })}
        onDensity={(next: Density) => update({ density: next })}
        onPageSize={(size) => onChange({ page_size: size })}
        automation={
          <>
            <TriggerSwitch automation={automation} onSaved={() => void loadMeta()} />
            <ScheduleEditor automation={automation} onSaved={() => void loadMeta()} />
            {automation ? <LinkBase url={automation.public_app_url} /> : null}
          </>
        }
      />

      <div
        className={`scrim${selectedId !== null ? ' is-on' : ''}`}
        onClick={() => setSelectedId(null)}
      />

      <DetailPanel
        bands={bands}
        sourceLabel={sourceLabel}
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
