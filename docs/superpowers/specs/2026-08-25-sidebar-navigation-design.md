# Sidebar navigation, settings surface, and monochrome theme

**Date:** 2026-08-25
**Status:** approved design, not yet planned
**Supersedes UI decisions in:** D19 (settings panel), D21 (dashboard sweep control)

## 1. Why

The dashboard grew four independent ways to narrow one list — stat tiles,
filter chips, view tabs, and the settings slide-out. Nothing tells the reader
which of the four is currently in force, and two of them express the same
thing in different words (the `Top scoring` tile and the `Score ≥ 70` chip).

Measured on the current build at a 950px viewport: the first tender begins at
**y=541**. Fifty-seven percent of the first screen is chrome, and the settings
panel is open by default (`state/preferences.ts:31`), covering the masthead
title, the whole first tile, and the left edge of every row.

This design promotes the tiles to navigation, moves configuration to its own
surface, and reduces the page to one filter model.

## 2. Navigation

A permanent left sidebar replaces the rail, the masthead, and the tile row.

```
TENDERS
  New this fetch      9     lens: first_seen within the most recent sweep
  Open opportunities  356   lens: active_only
  Top scoring         6     lens: active_only, minimum_score = goodFitBand
  Closing soon        6     lens: active_only, deadline day(0)..day(14)
  Needs review        3     lens: active_only, fit_statuses = [manual_review]
  All tenders         470   no lens; the audit root

SETTINGS
  Matching rules
  Sources             !1    badge = connectors reporting a problem

FOOTER
  Last sweep 15:44
  [Fetch new tenders] [Re-score]
```

### Decisions this encodes

**Connector problems is not a tender lens.** `state/views.ts` already declares
`TILES` with four entries and renders connector problems separately, because it
derives from source health rather than a tender query. It becomes a warning
badge on Sources, so the alert lives where the fix is.

**`New this fetch` is promoted and listed first.** It is the only view that
answers "what changed since I last looked", which is the daily job of a
monitoring product. It is a tab today.

**`All tenders` stays reachable.** The "Nothing is discarded — all 470 stored
and scored" promise requires a view that shows all 470.

**Counts are muted badges, not headline numerals.** The lenses overlap by
construction — one tender is Open and Top scoring and Closing soon at once, so
`356 + 6 + 6 + 3 != 470`. Small badges state the number without inviting
arithmetic that cannot reconcile.

**The masthead dissolves into the sidebar footer.** Title, last-sweep time, and
both operator actions move down. This plus the tile row is most of the 541px.

## 3. Filter model

One model replaces four.

- The **lens** comes from the sidebar and sets the floor. It renders as a
  locked chip: visible, explaining why the list is short, with no remove
  control.
- **Refinements** are the search box and every other chip. They stack on top
  of the lens and each carries a remove control.
- **Sort is never part of the lens.** `views.ts` deliberately omits `sort` from
  `OWNED`, on the reasoning that "ordering belongs to the reader and is
  orthogonal to which question they are asking". A lens may set an opening
  sort; it must never lock it, and changing sort must not change which lens is
  lit. That decision survives this redesign intact.
- The locked chip renders the lens's real predicate, not a fixed string.
  `Top scoring` shows `Score ≥ {goodFitBand}` — the band is configurable in
  Matching rules, so a hardcoded "70" would go stale the moment it is tuned.
- `Clear all` clears refinements only. The lens is where you *are*; it can only
  change by navigating.
- State lives in the URL: `/?lens=top-scoring&q=saas&score_min=85`. A filtered
  list is therefore linkable, which the current build cannot do.
- Refinements are remembered per lens in preferences. Returning to
  `Needs review` restores how it was left, and because the chip row always
  states the complete truth, a remembered filter cannot masquerade as an empty
  result.

The view tabs (`New this fetch / Relevant / Irrelevant / All stored`) are
deleted. Each is either a lens or a score range already expressible as a chip.

## 4. Visual direction

**Monochrome base.** Pure white surfaces, black type, a grey ramp for
secondary text and rules. Buttons are black fill with white type (primary) or
white with a black border (secondary). Dark mode is removed entirely: the
toggle, the `theme` preference, and every dark rule in `styles.css`.

**Colour is reserved for state.** It never appears on chrome, navigation,
buttons, or decoration. It appears only where the interface reports a
condition the reader must act on:

| Colour | Means | Appears on |
|---|---|---|
| Red | broken or expired | unavailable source, passed deadline |
| Amber | closing soon | deadline within 14 days |
| Green | good | healthy source, excellent fit |

Anything not in that table is black, white, or grey. The discipline is what
keeps the result reading as monochrome rather than as a light theme.

`views.ts` already carries a five-value `tone` per tile
(`brand | good | warning | serious | critical`). The table above is the
authority, and it maps onto those tones as:

| tone | renders |
|---|---|
| `brand` | neutral — black or grey. It is branding, not a condition. |
| `good` | green |
| `warning` | amber |
| `serious` | amber |
| `critical` | red |

Only `brand` collapses. The other four report a condition the reader acts on,
which is exactly what the palette reserves colour for.

Colour arrives as ink and hairline on a white ground — never as a filled
pill. A green chip becomes green text inside a green hairline on white. That
is what keeps a screen with six status chips still reading as monochrome.

**Type carries the identity.** The current build sets score badges, tile
counts, dates, and prose in one face at one width — confirmed via computed
style, all Inter. For a product whose entire value is a score, the numbers do
no work.

Proposal: **IBM Plex Mono for machine data** — scores, reference numbers,
published dates, source tags, counts — and Inter for prose. The split encodes
something true: mono means it came from the source or is a number, sans means
it was written for a human. Plex is institutional documentation type, which
suits procurement notices, and it is available from Google Fonts.

The score becomes the row's typographic anchor: set large in Plex Mono,
left-aligned as the entry point, replacing the current green rounded badge.
In a monochrome layout the score cannot rely on a coloured pill, so it relies
on size and face instead.

## 5. Settings — Matching rules

Edits a curated subset of `config/relevance_profiles.yaml` (866 lines). The
subset is what people tune; the rest stays in the file.

**Exposed**

- `weights`: `topic 0.55 / product_fit 0.30 / procurement_intent 0.15`, as
  sliders constrained to sum to 1.00. The file documents this contract in a
  comment; the UI enforces it.
- Thresholds: the 70 "highly relevant" line, `module_threshold`, and the
  `expired_multiplier` / `cancelled_multiplier`.
- Per-profile phrase lists: `strong / medium / weak` as add-remove chips.

**Not exposed.** `patterns:` regexes stay in the file. They are the sharp edge
and they are rarely touched.

**Input normalisation is enforced, not documented.** The file's matching
contract requires normalised text — lower-cased, accents folded, punctuation
replaced by spaces. Typing `cloud-based platform` normalises to
`cloud based platform` visibly as you type, so the trap the file warns about
stops existing.

**Saving is two-step.** Re-scoring rewrites the ranking under someone who may
have been working a shortlist for a week. Before applying:

> 34 tenders change score · 3 cross the 70 line · 1 leaves Top scoring

Confirm, then apply. Saving triggers a re-score, subject to the existing
`OPERATOR_RESCORE_COOLDOWN_SECONDS` (120) from D23.

## 6. Settings — Sources

One row per entry in `CONNECTOR_CLASSES`, all eight always listed, disabled
ones included.

```
TED           healthy       118 last sweep   [Fetch now]  [on]
US SAM.gov    unavailable   no API key       [Fetch now]  [on]
              Key ····AB12  [Replace]
CanadaBuys    healthy        31 last sweep   [Fetch now]  [on]
```

There is no "add a new source". `connectors/registry.py` hardcodes eight
Python classes with bespoke parsing per portal; a new source is a module plus
tests, not a form. The screen manages the sources that exist.

### Credentials

A credential row appears only on connectors that declare they need one (today:
SAM.gov). Behaviour, per the security decision below:

```
GET  /api/sources/{name}/credential  -> { configured: true, hint: "…AB12" }
PUT  /api/sources/{name}/credential  <- { value: "<key>" }  -> 204
```

- **Write-only.** The value is never returned by any endpoint. `GET` reports
  only whether one is configured, plus the last four characters.
- **Stored in `app_settings`** as `source.{name}.credential`, on the same rail
  as `scheduler.enabled`: the stored value beats `.env` and applies without a
  restart, which is what "wires in automatically" requires.
- **Guarded by `ALLOW_OPERATOR_ACTIONS`**, reusing the existing switch rather
  than introducing an auth system.
- Redaction: the key must be covered by `app/settings/config.py::redact()` and
  must never reach a log line or an error body.

### Security note — this is a real trade-off

D23 removed `require_cron_secret()` deliberately: the dashboard is
unauthenticated, and the two expensive writes are cost-controlled (409
single-flight, 429 cooldown, `ALLOW_OPERATOR_ACTIONS`) rather than
secret-gated. That reasoning holds because those actions are *expensive, not
confidential* — the damage ceiling is "someone triggers a sweep."

A credential inverts that property. Rate limits do not protect a secret, so
this design closes the read path instead: the key can be written and never
read. The residual risk is that anyone who can reach the dashboard can
*replace* a key. The blast radius is bounded — a wrong key breaks fetches for
that source, which surfaces immediately as a connector problem — but it is a
real exposure and it is accepted knowingly, not overlooked.

If the dashboard ever becomes reachable beyond a trusted network, credential
writes need a gate. That is out of scope here and should be its own decision.

## 7. Removed

- Dark mode: toggle, `theme` preference, and all dark rules in `styles.css`
  (currently 2310 lines covering both themes).
- The view tabs (`Views.tsx` was already deleted upstream; the tab row in
  `Toolbar` goes with it).
- The stat tile row (`StatTiles.tsx`) — becomes sidebar counts.
- The masthead (`Masthead.tsx`) — becomes the sidebar footer.
- The slide-out settings panel (`SettingsPanel.tsx`, `.slideout`) — becomes
  the per-lens filter surface plus the two Settings screens.
- `settingsOpen` from preferences.

## 8. Backend work

The frontend restructure is mostly frontend, but three items are not:

1. **Credential storage and resolution.** New `app_settings` keys, plus
   connector construction reading the stored value ahead of `Settings`.
   `registry.py::build_connector` currently takes settings only.
2. **Matching-rules read/write endpoints.** Reading and writing the curated
   subset of `relevance_profiles.yaml`, with validation (weights sum to 1.00,
   phrases normalise).
3. **Re-score preview.** Scoring the corpus against candidate rules without
   committing, to produce the "34 tenders change score" line. This is the
   largest single item and could ship in a second pass, with save going
   straight to re-score in the first.

## 9. Phasing

This spec is larger than one implementation plan. It should become three,
executed in order, each shippable on its own:

**Phase 1 — Theme.** Remove dark mode and apply the monochrome palette and the
type split, with no layout change. Isolated, visually verifiable, and it
shrinks the stylesheet before anyone restructures it. Nothing else depends on
it, so a regression here cannot corrupt later work.

**Phase 2 — Navigation and filter model.** Sidebar, lenses, locked chip, URL
state, and the deletion of tiles/tabs/masthead/slide-out. Frontend only. This
is the phase that delivers the actual goal.

**Phase 3 — Settings surfaces.** Sources with credentials, then Matching
rules. Both need backend work (§8) and are independent of each other, so they
can be split again if useful. Re-score preview is the tail end of this phase
and may slip to a fourth without blocking anything.

Phase 2 is the reason for the project. Phases 1 and 3 could each be dropped or
deferred without invalidating it.

## 10. Risks

- **The preview is expensive.** Scoring 470 tenders twice to diff them is
  cheap; at 50,000 it is not. Bound it or sample it, and say which in the
  implementation plan.
- **Per-lens remembered filters can confuse.** Mitigated by the chip row always
  stating the truth, but worth watching in use.
- **Stylesheet surgery.** Removing dark mode from a 2310-line stylesheet while
  restructuring layout is where regressions will come from. Do the theme
  removal as its own commit, verified visually, before layout work starts.

## 11. Testing

- `frontend/src/state/` — lens + refinement resolution, URL round-trip
  (serialise, parse, get the same state), per-lens persistence. Extends the
  existing `urlFilters.test.ts` and `views.test.ts`.
- Credential endpoints — a `GET` never returns the value; a `PUT` is refused
  with 403 when `ALLOW_OPERATOR_ACTIONS=false`; a stored value beats `.env`;
  the value never appears in a log line.
- Matching rules — weights that do not sum to 1.00 are refused; phrases
  normalise on input; a saved change re-scores.
- Visual: the sidebar at 1512 / 1024 / 390, first-tender position asserted
  above the fold.

## 12. Open

- Sort control placement once the toolbar shrinks.
- Whether `Needs review` should support an explicit resolve action, which
  would make it a queue rather than a lens. Out of scope; worth revisiting.
