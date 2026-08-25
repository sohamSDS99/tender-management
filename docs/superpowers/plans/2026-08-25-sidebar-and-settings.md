# Phases 2 & 3 Implementation Plan

**Spec:** `docs/superpowers/specs/2026-08-25-sidebar-navigation-design.md`

**Note on format.** This plan records task boundaries, interfaces, and the
decisions each task locks in, rather than reproducing every line of code as a
handoff plan would. It was written to be executed immediately by its author in
the same session, so full code blocks would be written twice. Phase 1's plan
(`2026-08-25-monochrome-theme.md`) is the full-ceremony example.

## Global constraints

- Colour only on status; monochrome everywhere else (Phase 1's palette stands).
- Sort is never owned by a lens (`views.ts` `OWNED` reasoning).
- The chip row always states the complete truth about what is on screen.
- Credentials are write-only and never returned by any endpoint.
- Every task ends green: `npx vitest run` + `tsc --noEmit`, and `pytest -q`
  for backend tasks.

---

# Phase 2 — Navigation and filter model

### Task 2.1 — The lens model

**Files:** create `state/lenses.ts`, `state/lenses.test.ts`; retire
`state/views.ts` (`VIEWS`, `TILES`, `activeView`) once callers move.

Collapses `VIEWS` (4 tabs) and `TILES` (4 tiles) into one `LENSES` array of
six, in sidebar order: `new`, `open`, `topscoring`, `closing`, `review`,
`all`.

**Produces:**
- `LENSES: LensSpec[]` where `LensSpec = { key, label, tone, unavailable?, patch(ctx), lockedLabel(ctx), count(stats) }`
- `activeLens(filters, ctx): LensKey | null` — the existing `activeView`
  matching logic, widened to the six.
- `lensByKey(key): LensSpec | undefined`

`lockedLabel` is why this is not just a rename: the locked chip must render the
lens's real predicate (`Score ≥ {goodFitBand}`), not a fixed string.

### Task 2.2 — Lens in the URL

**Files:** `state/urlFilters.ts`, `state/urlFilters.test.ts`

`filtersFromSearch` gains `lens`, `searchFromFilters` writes it. A URL with a
lens resolves filters as `lens.patch(ctx)` first, then refinements over the
top, so `?lens=top-scoring&score_min=85` is coherent.

Round-trip test: serialise → parse → identical state.

### Task 2.3 — Locked chip

**Files:** `state/urlFilters.ts` (`activeChips`), `components/Toolbar.tsx`

`activeChips` gains a `locked: boolean`. The lens's own predicate renders
first, locked, with no remove control; refinements follow as today. `Clear all`
clears refinements only.

### Task 2.4 — Sidebar component

**Files:** create `components/Sidebar.tsx`; delete `components/Rail.tsx`,
`components/StatTiles.tsx`, `components/Masthead.tsx`

One permanent left column: brand, the six lenses with muted counts, the two
Settings links with a problem badge on Sources, and a footer carrying
last-sweep plus Fetch/Re-score. Replaces the rail, the tile row, and the
masthead in a single component.

### Task 2.5 — Rewire the Dashboard and drop the tabs

**Files:** `pages/Dashboard.tsx`, `components/Toolbar.tsx`

Dashboard renders `<Sidebar>` + content. The tab row leaves `Toolbar`. Per-lens
refinements are remembered in preferences under `lensFilters: Record<LensKey, …>`.

### Task 2.6 — Layout CSS

**Files:** `styles.css`

`.shell` padding-left moves from `--rail-w` to `--sidebar-nav-w`. Sidebar
rules added; `.rail`, `.stats`, `.mast`, `.tabsbar` rules deleted. Verify the
first tender clears the fold at 1512×950.

---

# Phase 3 — Settings surfaces

### Task 3.1 — Credential storage (backend)

**Files:** `app/services/credentials.py` (new), `app/connectors/base.py`,
`app/connectors/registry.py`, `tests/test_credentials.py` (new)

`app_settings` key `source.{name}.credential`. Resolution order: stored value,
then `Settings` (i.e. `.env`). `TenderConnector` gains
`credential_key: str | None` declaring whether the connector needs one;
`SamGovConnector` sets it.

Tests: stored beats env; absent falls back to env; the value never appears in
a log line; `redact()` covers it.

### Task 3.2 — Credential endpoints (backend)

**Files:** `app/api/routes.py`, `app/schemas.py`, `tests/test_credentials.py`

```
GET /api/sources/{name}/credential -> {configured: bool, hint: str | None}
PUT /api/sources/{name}/credential <- {value: str} -> 204
```

`PUT` is refused 403 when `ALLOW_OPERATOR_ACTIONS=false`, 404 for a source
that declares no credential. `GET` never returns the value — asserted.

### Task 3.3 — Matching rules read/write (backend)

**Files:** `app/services/matching_rules.py` (new), `app/api/routes.py`,
`tests/test_matching_rules.py` (new)

Reads the curated subset of `config/relevance_profiles.yaml` — `weights`,
thresholds, per-profile phrase lists — and writes it back preserving
everything else in the file, comments included. Validation: weights sum to
1.00; phrases normalise on the way in.

```
GET /api/matching-rules -> {weights, thresholds, profiles}
PUT /api/matching-rules <- same shape -> 204, then re-score
```

### Task 3.4 — Sources screen (frontend)

**Files:** `components/SourcesSettings.tsx` (new), `pages/Dashboard.tsx`

One row per connector: status, last-sweep count, fetch-now, enable toggle, and
a credential field on connectors that declare one. Write-only: the field shows
`····AB12` and a Replace control, never the value.

### Task 3.5 — Matching rules screen (frontend)

**Files:** `components/MatchingRulesSettings.tsx` (new)

Weight sliders constrained to sum to 1.00, threshold inputs, phrase chips per
profile with normalisation shown live.

### Task 3.6 — Re-score preview

**Files:** `app/services/matching_rules.py`, `app/api/routes.py`

```
POST /api/matching-rules/preview <- candidate rules
  -> {changed: int, crossing_up: int, crossing_down: int}
```

Scores the corpus against candidate rules without committing. Bounded: if the
corpus exceeds 5000, sample and label the result as an estimate.

**This task may be dropped.** If it is, save goes straight to re-score and the
confirm step states the count of tenders that will be re-scored instead of
what will change.
