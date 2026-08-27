import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api } from '../api/client';
import type {
  AutomationStatus,
  Density,
  FeedbackResponse,
  FetchRun,
  SourceStatus,
  Stats,
  TenderFilters,
  TenderPage,
  Verdict,
} from '../types';
import {
  DEFAULT_FILTERS,
  activeChips,
  activeFilterCount,
  correctedPage,
  filtersFromSearch,
  searchFromFilters,
} from '../state/urlFilters';
import { OWNED, activeLens, lensByKey, type LensContext, type LensKey } from '../state/lenses';
import { usePreferences } from '../state/preferences';
import { useAuth } from '../state/auth';
import {
  categoryFor,
  settingsFromSearch,
  withSettings,
  type SettingsKey,
} from '../state/settingsNav';
import {
  FALLBACK_BANDS,
  FALLBACK_SWEEP_DAYS,
  countryLabel,
  feedbackMessage,
  isSweepInFlight,
  deploymentLabel,
  fitLabel,
  makeSourceLabel,
} from '../labels';
import { DetailPanel } from '../components/DetailPanel';
import { BucketNote, Notice } from '../components/Notice';
import { Pager } from '../components/Pager';
import { RunsTable } from '../components/RunsTable';
import { SettingsPanel } from '../components/SettingsPanel';
import { SourcesPanel } from '../components/SourcesPanel';
import { SweepReport } from '../components/SweepReport';
import { AccountSettings } from '../components/settings/AccountSettings';
import { AuthDialog } from '../components/auth/AuthDialog';
import { AutomationSettings } from '../components/settings/AutomationSettings';
import { DisplaySettings } from '../components/settings/DisplaySettings';
import { SourcesSettings } from '../components/settings/SourcesSettings';
import { SystemSettings } from '../components/settings/SystemSettings';
import { MatchingRulesSettings } from '../components/MatchingRulesSettings';
import { Sidebar } from '../components/Sidebar';
import { TenderList } from '../components/TenderList';
import { Toolbar } from '../components/Toolbar';

/**
 * The whole filter set lives in the URL, so any view is shareable and survives a
 * refresh — and so a Slack digest can link to a filtered dashboard, not only to
 * `?tender=<id>`.
 */
const initial = filtersFromSearch(window.location.search);
/** Which settings page the URL asks for, so one survives a refresh or a share. */
const initialSettings = settingsFromSearch(window.location.search);

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
  const [reloadToken, setReloadToken] = useState(0);

  // Which whole-system action is in flight, and what the server said about it.
  const [busy, setBusy] = useState<'fetch' | 'rescore' | null>(null);
  const [busySource, setBusySource] = useState<string | null>(null);
  /** The notice whose verdict is being written, so only its own control waits. */
  const [verdictBusy, setVerdictBusy] = useState<number | null>(null);
  const [actionMessage, setActionMessage] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(
    null,
  );
  // The sweep this reader started: which batch it is and how deep it searched.
  // Tracked because a sweep runs for minutes after the request returns, and the
  // page previously said "started" once and then never mentioned it again.
  const [sweep, setSweep] = useState<{ batchId: string | null; daysBack: number } | null>(null);
  // How far back the next sweep looks. The server owns the default (it is the
  // number that decides whether Fetch can find anything at all), so this is
  // seeded from /api/automation rather than kept as a second copy.
  const [sweepDays, setSweepDays] = useState<number>(FALLBACK_SWEEP_DAYS);
  const depthTouched = useRef(false);

  // Settings navigation: which full-width page is showing, and whether the
  // category menu is open. The filters panel keeps its own stored preference,
  // because it is the one surface that stays open while you work.
  const [settingsPage, setSettingsPage] = useState<SettingsKey | null>(initialSettings);
  const auth = useAuth();
  const [authOpen, setAuthOpen] = useState(false);

  // Arriving on an invitation link opens the form with the token already held.
  // Without this the invitee lands on a dashboard indistinguishable from the
  // public one and has to work out that the link they followed did anything —
  // and the token has by then been stripped from the address bar, so there is
  // no second chance to notice it.
  useEffect(() => {
    if (auth.status === 'ready' && auth.inviteToken && !auth.user) setAuthOpen(true);
  }, [auth.status, auth.inviteToken, auth.user]);

  const { preferences, update } = usePreferences();
  const requestId = useRef(0);

  // --- URL <-> state ------------------------------------------------------
  useEffect(() => {
    // The settings page rides on top of the filter codec rather than inside it,
    // so there is still exactly one place that knows how filters serialise.
    const search = withSettings(searchFromFilters(filters, selectedId), settingsPage);
    const next = `${window.location.pathname}${search ? `?${search}` : ''}`;
    if (next !== `${window.location.pathname}${window.location.search}`) {
      window.history.replaceState(null, '', next);
    }
  }, [filters, selectedId, settingsPage]);

  useEffect(() => {
    const onPop = () => {
      const parsed = filtersFromSearch(window.location.search);
      setFilters(parsed.filters);
      setSelectedId(parsed.tenderId);
      setSettingsPage(settingsFromSearch(window.location.search));
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
   * The server owns the sweep depth, so adopt it once it answers — but never
   * overwrite a choice the reader has already made.
   */
  useEffect(() => {
    if (!automation || depthTouched.current) return;
    setSweepDays(automation.operator_fetch_days_back);
  }, [automation]);

  const chooseSweepDays = useCallback((days: number) => {
    depthTouched.current = true;
    setSweepDays(days);
  }, []);

  /**
   * A sweep runs for many minutes in the background, so the 202 only means
   * "started". Poll the metadata while it runs rather than leaving the page
   * claiming the old numbers — this is what makes the progress line move.
   */
  // `queued` counts: a batch's rows are all queued for the first instant, and
  // treating that as "finished" stopped the poll before the sweep had started.
  const sweeping = busy === 'fetch' || isSweepInFlight(automation?.last_run?.status);
  useEffect(() => {
    if (!sweeping) return;
    const timer = window.setInterval(() => void loadMeta(), 8_000);
    return () => window.clearInterval(timer);
  }, [sweeping, loadMeta]);

  const runAction = useCallback(
    async (kind: 'fetch' | 'rescore', source?: string) => {
      setBusy(kind);
      if (source) setBusySource(source);
      setActionMessage(null);
      try {
        if (kind === 'fetch') {
          // The depth is the point of the call. Sending nothing let the backend
          // fall back to the scheduler's 72-hour window, which by the time
          // anyone presses this button holds nothing it has not already stored.
          const started = await api.fetchNow({
            sources: source ? [source] : undefined,
            daysBack: sweepDays,
          });
          // SweepReport takes over from here and reports what it finds.
          setSweep({ batchId: started.batch_id, daysBack: started.days_back });
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
        // A refused sweep is not a running sweep: leaving the report up would
        // have it narrate a batch that never started.
        if (kind === 'fetch') setSweep(null);
      } finally {
        setBusy(null);
        setBusySource(null);
      }
    },
    [loadMeta, sweepDays],
  );

  // --- verdicts (D26) -----------------------------------------------------
  /**
   * Record or withdraw one verdict, and say what it changed.
   *
   * The list and the counts both move, so both are reloaded: the marked notice
   * usually leaves the current view entirely, and a page that silently lost a
   * row would read as a glitch rather than as the thing just asked for. The
   * message is what makes the learning visible at the one moment it happens.
   */
  const announceVerdict = useCallback(
    (result: FeedbackResponse) => {
      setActionMessage({ tone: 'ok', text: feedbackMessage(result) });
      setReloadToken((v) => v + 1);
      void loadMeta();
    },
    [loadMeta],
  );

  const applyVerdict = useCallback(
    async (id: number, verdict: Verdict | null) => {
      setVerdictBusy(id);
      setActionMessage(null);
      try {
        const result =
          verdict === null ? await api.clearFeedback(id) : await api.setFeedback(id, verdict);
        announceVerdict(result);
      } catch (err) {
        setActionMessage({
          tone: 'bad',
          text: err instanceof ApiError ? err.message : String(err),
        });
      } finally {
        setVerdictBusy(null);
      }
    },
    [announceVerdict],
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
      setSettingsPage(null);
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
   * own predicate first, locked, then anything narrowed on top of it.
   *
   * Locked because the lens is where you *are*. It changes by navigating, not
   * by dismissing a chip — which is why "Clear all" only clears the rest.
   */
  const chipRow = useMemo(() => {
    const lens = lensByKey(currentLens);
    const locked = lens?.lockedLabel(lensContext) ?? null;
    const refinements: { label: string; locked?: boolean; onRemove: () => void }[] = (
      currentLens ? chips.filter((chip) => !OWNED.includes(chip.key as never)) : chips
    ).map((chip) => ({ label: chip.label, onRemove: () => onChange(chip.clear) }));
    return locked === null
      ? refinements
      : [{ label: locked, locked: true, onRemove: () => {} }, ...refinements];
  }, [chips, currentLens, lensContext, onChange]);

  const brokenSources = useMemo(
    () => sources.filter((s) => s.unavailable_reason || s.last_status === 'failed').length,
    [sources],
  );

  const lensNote = useMemo(() => {
    const view = lensByKey(currentLens);
    return view ? view.note(lensContext) : null;
  }, [currentLens, lensContext]);

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

  /**
   * Open a category on the surface it belongs to.
   *
   * Filters get the side panel because the results have to stay visible while
   * they are being set; everything else takes the width. The two are mutually
   * exclusive on purpose - the panel is fixed against the rail and would sit on
   * top of a page, hiding the very thing the reader just asked for.
   */
  const selectCategory = useCallback(
    (key: SettingsKey) => {
      if (categoryFor(key)?.surface === 'panel') {
        setSettingsPage(null);
        update({ settingsOpen: true });
      } else {
        update({ settingsOpen: false });
        setSettingsPage(key);
      }
    },
    [update],
  );

  const closeSettingsPage = useCallback(() => setSettingsPage(null), []);

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

  const settingsSurface =
    settingsPage === 'display' ? (
      <DisplaySettings
        density={preferences.density}
        pageSize={filters.page_size}
        onDensity={(next: Density) => update({ density: next })}
        onPageSize={(size) => onChange({ page_size: size })}
        onBack={closeSettingsPage}
      />
    ) : settingsPage === 'rules' ? (
      <MatchingRulesSettings
        onBack={closeSettingsPage}
        onRescored={() => {
          void loadMeta();
          setReloadToken((v) => v + 1);
        }}
      />
    ) : settingsPage === 'automation' ? (
      <AutomationSettings
        automation={automation}
        onSaved={() => void loadMeta()}
        onBack={closeSettingsPage}
      />
    ) : settingsPage === 'sources' ? (
      <SourcesSettings
        sources={sources}
        busySource={busySource}
        onFetchSource={(name) => void runAction('fetch', name)}
        onChanged={() => void loadMeta()}
        onBack={closeSettingsPage}
      />
    ) : settingsPage === 'account' ? (
      <AccountSettings auth={auth} onBack={closeSettingsPage} />
    ) : settingsPage === 'system' ? (
      <SystemSettings
        automation={automation}
        stats={stats}
        onReload={() => void loadMeta()}
        onBack={closeSettingsPage}
      />
    ) : null;

  return (
    <>
      <div className="shell">
        {settingsSurface}

        {settingsSurface ? null : (
          <>
            <div className="col">
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

              {sweep ? (
                <SweepReport
                  daysBack={sweep.daysBack}
                  batchId={sweep.batchId}
                  lastRun={automation?.last_run ?? null}
                  runs={runs}
                  onShowNew={() => selectLens('new')}
                  onDismiss={() => setSweep(null)}
                />
              ) : null}

              {actionMessage ? (
                <p
                  className={`notice${actionMessage.tone === 'bad' ? ' notice--bad' : ' notice--ok'}`}
                  role="status"
                >
                  {actionMessage.text}
                </p>
              ) : null}

              <Notice automation={automation} sweeping={sweeping} />

              <main>
                {lensNote ? <BucketNote text={lensNote} /> : null}

                <div className="results__head">
                  <h2 aria-live="polite">
                    {/* Suppressed while erroring: the last successful count is stale,
                      and showing "6 tenders" above "cannot reach the API"
                      contradicts itself. */}
                    {page && !error ? (
                      <>
                        {page.total.toLocaleString('en-GB')}{' '}
                        {page.total === 1 ? 'tender' : 'tenders'}
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
                  onVerdict={(id, verdict) => void applyVerdict(id, verdict)}
                  verdictBusy={verdictBusy}
                />

                {page && !error ? (
                  <Pager
                    page={page}
                    onGo={(next) => setFilters((prev) => ({ ...prev, page: next }))}
                  />
                ) : null}
              </main>

              <RunsTable runs={runs} sourceLabel={sourceLabel} />
            </div>
          </>
        )}
      </div>

      <Sidebar
        stats={stats}
        automation={automation}
        lensContext={lensContext}
        activeLens={currentLens}
        settingsKey={settingsPage ?? (settingsOpen ? 'filters' : null)}
        brokenSources={brokenSources}
        busy={busy}
        sweepDays={sweepDays}
        user={auth.user}
        authStatus={auth.status}
        onSweepDays={chooseSweepDays}
        onSelectLens={selectLens}
        onSelectCategory={selectCategory}
        onFetch={() => void runAction('fetch')}
        onRescore={() => void runAction('rescore')}
        onSignIn={() => setAuthOpen(true)}
        onSignOut={() => void auth.signOut()}
      />

      <AuthDialog auth={auth} open={authOpen} onClose={() => setAuthOpen(false)} />

      <SettingsPanel
        open={settingsOpen}
        filters={filters}
        stats={stats}
        sources={sources}
        total={page?.total ?? 0}
        onChange={onChange}
        onReset={clearAll}
        onClose={() => update({ settingsOpen: false })}
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
        onFeedback={announceVerdict}
      />
    </>
  );
}
