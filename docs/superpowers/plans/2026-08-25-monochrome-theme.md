# Monochrome Theme Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove dark mode entirely and repaint the dashboard in a monochrome palette with a sans/mono type split, changing no layout.

**Architecture:** `styles.css` is already fully tokenised — `:root` holds the dark palette and `html[data-theme='light']` overrides the same 37 custom properties, with only three theme-specific rules living outside those two blocks. So this is a token rewrite plus the removal of the theme state machine, not a sweep through 2300 lines. Work inward: delete the state machinery first (it has real tests), then collapse the tokens, then apply type.

**Tech Stack:** React 18 + TypeScript, Vite, vitest + jsdom, plain CSS custom properties.

**Spec:** `docs/superpowers/specs/2026-08-25-sidebar-navigation-design.md` (§4 Visual direction, §7 Removed)

## Global Constraints

- **No layout change in this phase.** No element moves, resizes, or is added. Only colour, type, and the deletion of the theme toggle. Layout is Phase 2.
- **Pure white surfaces, black type.** Buttons are black fill / white type (primary) or white with a black border (secondary).
- **Colour appears only on status** — never on chrome, navigation, buttons, or decoration. The permitted set is exactly: green (`good`), amber (`warning`, `serious`), red (`critical`).
- **Colour arrives as ink and hairline on white** — never a filled pill.
- **`tone` mapping:** `brand`→neutral, `good`→green, `warning`→amber, `serious`→amber, `critical`→red.
- Do not touch `views.ts` `TILES`/`VIEWS`, the tab row, the tile row, or `SettingsPanel`. They change in Phase 2.
- Every task ends green: `npx vitest run` in `frontend/`.

## File Structure

| File | Change | Responsibility after |
|---|---|---|
| `frontend/src/state/preferences.ts` | Modify | Density and `settingsOpen` only. No theme, no OS listener. |
| `frontend/src/state/preferences.test.ts` | Rewrite | Tests persistence and the tolerance of stale stored keys. |
| `frontend/src/types/index.ts` | Modify | `Theme` type and `Preferences.theme` deleted. |
| `frontend/src/components/Masthead.tsx` | Modify | Same markup minus the theme button and its two props. |
| `frontend/src/pages/Dashboard.tsx` | Modify | Stops passing theme props. |
| `frontend/src/styles.css` | Modify | One `:root` token block. No `[data-theme]` selectors. |
| `frontend/index.html` | Modify | Drops the anti-flash theme stamp if present. |

---

### Task 1: Strip the theme state machine

**Files:**
- Modify: `frontend/src/state/preferences.ts`
- Modify: `frontend/src/types/index.ts:220`
- Test: `frontend/src/state/preferences.test.ts` (rewrite)

**Interfaces:**
- Consumes: nothing.
- Produces: `Preferences = { density: Density; settingsOpen: boolean }`, `DEFAULT_PREFERENCES`, `readPreferences(): Preferences`, `usePreferences()`. The exports `Theme`, `resolveTheme`, `resolveWithSystem`, `toggledTheme`, and `prefersDark` no longer exist.

Note on stored data: `readPreferences` rebuilds the object field by field, so a `theme` key left in `localStorage` from a previous version is ignored without error. **Do not bump `STORAGE_KEY`** — bumping it would also discard the reader's `density` and `settingsOpen` for no reason.

- [ ] **Step 1: Replace the test file with one that covers what remains**

Replace the whole contents of `frontend/src/state/preferences.test.ts`:

```typescript
import { describe, expect, it, beforeEach } from 'vitest';
import { DEFAULT_PREFERENCES, readPreferences } from './preferences';

const KEY = 'tender-monitor:preferences:v2';

describe('readPreferences', () => {
  beforeEach(() => window.localStorage.clear());

  it('returns the defaults when nothing is stored', () => {
    expect(readPreferences()).toEqual(DEFAULT_PREFERENCES);
  });

  it('ignores a stale theme key left by an older version', () => {
    window.localStorage.setItem(
      KEY,
      JSON.stringify({ theme: 'dark', density: 'compact', settingsOpen: false }),
    );
    const prefs = readPreferences();
    expect(prefs).toEqual({ density: 'compact', settingsOpen: false });
    expect('theme' in prefs).toBe(false);
  });

  it('falls back per field when a stored value is invalid', () => {
    window.localStorage.setItem(KEY, JSON.stringify({ density: 'enormous' }));
    expect(readPreferences().density).toBe(DEFAULT_PREFERENCES.density);
  });

  it('survives a corrupt value rather than throwing', () => {
    window.localStorage.setItem(KEY, '{not json');
    expect(readPreferences()).toEqual(DEFAULT_PREFERENCES);
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && npx vitest run src/state/preferences.test.ts`
Expected: FAIL — `readPreferences()` still returns a `theme` field, so the `toEqual` in test 2 fails.

- [ ] **Step 3: Delete the theme code from `preferences.ts`**

Delete outright: `isTheme`, `resolveTheme`, `resolveWithSystem`, `toggledTheme`, `prefersDark`, the `systemDark` state, and the second `useEffect` that subscribes to `matchMedia`.

`DEFAULT_PREFERENCES` becomes:

```typescript
export const DEFAULT_PREFERENCES: Preferences = {
  density: 'comfortable',
  settingsOpen: true,
};
```

`readPreferences`'s return becomes:

```typescript
    return {
      density: isDensity(parsed.density) ? parsed.density : DEFAULT_PREFERENCES.density,
      settingsOpen:
        typeof parsed.settingsOpen === 'boolean'
          ? parsed.settingsOpen
          : DEFAULT_PREFERENCES.settingsOpen,
    };
```

The remaining effect in `usePreferences` loses its theme line and its `systemDark` dependency:

```typescript
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.density = preferences.density;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
    } catch {
      /* storage unavailable: the attribute above still applied */
    }
  }, [preferences]);
```

Also delete the file-header comment's reference to `html[data-theme="dark"]` and the block comment above `DEFAULT_PREFERENCES` explaining why dark is the default — it documents a decision that no longer exists.

- [ ] **Step 4: Remove the type**

In `frontend/src/types/index.ts`, delete the `theme: Theme;` line from `Preferences` and delete the `Theme` type declaration itself.

- [ ] **Step 5: Run the test file — it should pass**

Run: `cd frontend && npx vitest run src/state/preferences.test.ts`
Expected: PASS, 4 tests.

- [ ] **Step 6: Typecheck to find every remaining caller**

Run: `cd frontend && npx tsc --noEmit`
Expected: errors in `Masthead.tsx` and `Dashboard.tsx` only. Those are Task 2. Do not fix them here.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/state/preferences.ts frontend/src/state/preferences.test.ts frontend/src/types/index.ts
git commit -m "Remove the theme preference and its OS listener"
```

---

### Task 2: Remove the toggle from the UI

**Files:**
- Modify: `frontend/src/components/Masthead.tsx`
- Modify: `frontend/src/pages/Dashboard.tsx`
- Modify: `frontend/index.html`
- Modify: `frontend/src/styles.css:282,520,1253`

**Interfaces:**
- Consumes: `Preferences` from Task 1, which no longer has `theme`.
- Produces: `Masthead` without the `theme`, `preference`, or `onToggleTheme` props.

- [ ] **Step 1: Delete the button from `Masthead.tsx`**

Remove the `<button>` at roughly lines 92–99 (the one whose `aria-label` is `Switch to ${...} theme`). Remove `theme`, `preference`, and `onToggleTheme` from both the destructured parameters and the props type. Remove `Theme` from the `import type` on line 1.

- [ ] **Step 2: Stop passing them in `Dashboard.tsx`**

Remove the `theme=`, `preference=`, and `onToggleTheme=` attributes from the `<Masthead ... />` call, and delete the toggle handler that fed `onToggleTheme` along with any now-unused import from `preferences`.

- [ ] **Step 3: Delete the three theme-specific CSS rules**

In `frontend/src/styles.css`, delete these rules entirely:

- `html[data-theme='dark'] .railbtn.is-on` (line ~282) — `.railbtn.is-on` gets its colour from the base rule in Task 3.
- `html[data-theme='dark'] .logo` (line ~520) — same.
- `html[data-theme='light'] .sk::after` (line ~1253) — but first move its white-tinted gradient into the unprefixed `.sk::after` rule, replacing whatever dark gradient is there. The skeleton shimmer must be the light one now.

- [ ] **Step 4: Trim the anti-flash stamp — do not delete it**

`frontend/index.html:20-40` holds an inline script that stamps **both** theme and density before first paint. Density still exists, so the script stays; only the theme half goes. Replace the whole `<script>` block with:

```html
    <script>
      // Stamp density before first paint, so the stylesheet's default is not
      // painted and then replaced once React mounts.
      (function () {
        try {
          var raw = localStorage.getItem('tender-monitor:preferences:v2');
          var saved = raw ? JSON.parse(raw) : {};
          document.documentElement.dataset.density =
            saved.density === 'compact' ? 'compact' : 'comfortable';
        } catch (e) {
          /* :root carries the comfortable default */
        }
      })();
    </script>
```

Deleting the block outright would silently break density stamping and reintroduce a flash.

- [ ] **Step 5: Typecheck and test**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: zero type errors; all tests pass.

- [ ] **Step 6: Confirm no theme references survive**

Run: `cd frontend/src && grep -rn "data-theme\|toggledTheme\|resolveTheme\|prefersDark\|Theme" . | grep -v prefers-color`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/Masthead.tsx frontend/src/pages/Dashboard.tsx frontend/src/styles.css frontend/index.html
git commit -m "Remove the theme toggle and the last data-theme rules"
```

---

### Task 3: Collapse the palette to one monochrome token set

**Files:**
- Modify: `frontend/src/styles.css:10-104`

**Interfaces:**
- Consumes: a stylesheet with no `[data-theme]` selectors left (Task 2).
- Produces: a single `:root` block. Every custom property keeps its existing name, so no rule elsewhere in the file needs editing.

The whole point of this task is that names stay put. `--brand` still exists; it is now black instead of blue. Rules that consume it need no change.

- [ ] **Step 1: Replace both token blocks with one**

Delete the `html[data-theme='light'] { ... }` block (lines ~62–104) entirely. Replace the custom properties inside `:root` (lines ~10–56) with:

```css
  color-scheme: light;

  /* Surfaces: white, with two barely-there steps for nesting. */
  --page: #ffffff;
  --surface: #ffffff;
  --surface-2: #fafafa;
  --surface-3: #f4f4f5;

  /* Ink. */
  --ink: #000000;
  --ink-soft: #52525b;
  --ink-muted: #71717a;

  /* Rules. */
  --line: #e4e4e7;
  --line-strong: #d4d4d8;

  /* Action is black, not a hue. --brand keeps its name so no rule moves. */
  --brand: #000000;
  --brand-hover: #27272a;
  --brand-ink: #ffffff;
  --brand-soft: #f4f4f5;
  --brand-ring: rgba(0, 0, 0, 0.16);

  /* Status marks. The only colour in the system. */
  --mark-good: #15803d;
  --mark-warning: #b45309;
  --mark-serious: #b45309;
  --mark-critical: #b91c1c;

  /* Status chips: coloured ink and hairline on white, never a filled pill. */
  --green-bg: #ffffff;
  --green-ink: #15803d;
  --amber-bg: #ffffff;
  --amber-ink: #b45309;
  --red-bg: #ffffff;
  --red-ink: #b91c1c;
  --blue-bg: #ffffff;
  --blue-ink: #000000;
  --grey-bg: #ffffff;
  --grey-ink: #52525b;

  /* Score ramp, now a value ramp rather than a hue ramp. */
  --seq-100: #d4d4d8;
  --seq-250: #a1a1aa;
  --seq-450: #52525b;
  --seq-600: #000000;

  --radius: 12px;
  --radius-sm: 8px;
  --radius-xs: 6px;

  /* Shadows on white have to be far lighter than the dark theme's. */
  --shadow-1: 0 1px 2px rgba(0, 0, 0, 0.06);
  --shadow-2: 0 4px 6px -1px rgba(0, 0, 0, 0.07), 0 10px 24px -8px rgba(0, 0, 0, 0.08);
  --shadow-3: 0 24px 60px -12px rgba(0, 0, 0, 0.14);
  --ease: cubic-bezier(0.32, 0.72, 0, 1);
```

Keep any custom property that exists in the current `:root` but is not listed above — give it a monochrome value in the same spirit rather than deleting it, since some rule consumes it.

- [ ] **Step 2: Give the status chips their hairline**

The chips read as filled pills because `--*-bg` carried the colour. Every `--*-bg` is now white, so they need a hairline in their own ink to stay bounded objects. At `styles.css:1085-1098`, the four `.badge--*` rules become:

```css
.badge--green {
  background: var(--green-bg);
  color: var(--green-ink);
  border: 1px solid currentColor;
}
.badge--amber {
  background: var(--amber-bg);
  color: var(--amber-ink);
  border: 1px solid currentColor;
}
.badge--red {
  background: var(--red-bg);
  color: var(--red-ink);
  border: 1px solid currentColor;
}
.badge--grey {
  background: var(--grey-bg);
  color: var(--grey-ink);
  border: 1px solid var(--line-strong);
}
```

`--grey-ink` is body-text grey; a hairline in it would read as a black box, so grey chips take `--line-strong` instead.

A 1px border on a previously borderless chip adds 2px to its box. Check `.badge`'s own rule — if it sets `padding`, reduce it by 1px on each axis so the chip's outer size is unchanged, per the no-layout-change constraint.

- [ ] **Step 3: Look at it**

Start the dev servers if they are not running, then:

```bash
node <scratch>/shot.js http://localhost:5174/ 1512 950 /tmp/mono.png \
  "document.querySelector('button[aria-label=\"Hide settings\"]')?.click(); 'ok'"
```

Open the PNG. Check specifically: no blue survives anywhere; primary buttons are black on white; status chips read as outlined, not filled; text on white is legible at every weight; nothing has vanished by turning white-on-white.

- [ ] **Step 4: Fix the known white-on-white casualty, then whatever the screenshot shows**

One is known in advance: `.btn--danger:hover` at `styles.css:406` sets `background: var(--red-bg)`, which is now white — so the destructive button loses all hover feedback. Give it a tint that is not in the token set precisely because nothing else needs one:

```css
.btn--danger:hover:not(:disabled) {
  background: #fef2f2;
  border-color: var(--red-ink);
}
```

Then re-screenshot. Any other element that has vanished is consuming a token that is now white against white; give it `--line` as a border or `--surface-2` as a ground. Repeat until clean.

- [ ] **Step 5: Test**

Run: `cd frontend && npx vitest run`
Expected: PASS. (No test asserts colour; this confirms nothing else broke.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/styles.css
git commit -m "Collapse the two palettes into one monochrome token set"
```

---

### Task 4: Split the type between prose and machine data

**Files:**
- Modify: `frontend/src/styles.css:117` (the sans stack), `:181,1327,2209,2229` (existing mono declarations)

**Interfaces:**
- Consumes: the `:root` block from Task 3.
- Produces: `--font-sans` and `--font-mono` tokens.

A mono role already exists at four selectors, hardcoding `ui-monospace, SFMono-Regular, Menlo, monospace`. This task tokenises it and extends it to the score.

**Decision — no web font.** The spec proposed IBM Plex Mono. The app currently loads **no** web fonts at all, and it ships in Docker where a Google Fonts request may not resolve; adding one would make type rendering depend on the network. The system mono stack already delivers the sans/mono distinction the split is for. Tokenising it means adopting Plex later is a one-line change to `--font-mono` plus a `<link>`.

- [ ] **Step 1: Add the two tokens**

In the `:root` block, beside the radius tokens:

```css
  --font-sans: Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
```

- [ ] **Step 2: Point the existing declarations at them**

At `styles.css:117`, replace the multi-line `font-family:` value with `font-family: var(--font-sans);`.

At each of lines ~181, ~1327, ~2209, ~2229, replace `font-family: ui-monospace, SFMono-Regular, Menlo, monospace;` with `font-family: var(--font-mono);`.

- [ ] **Step 3: Move the score off hue and onto value**

`.score` at `styles.css:1152` is a 52×52 box, and `.score--green` / `--amber` / `--red` at 1168–1182 fill it with status colour. That is wrong under this palette twice over: a score is not a status, and status colour is reserved.

**The box stays exactly as it is.** Making the score a bare typographic anchor removes a 52px element and reflows the row — that is Phase 2 work, not this phase. Here it only changes face and colour.

In `.score`, change the type and leave every box property (`display`, `width`, `height`, `border-radius`, `place-items`) untouched:

```css
  font-family: var(--font-mono);
  font-weight: 500;
  font-size: 1.2rem;
  letter-spacing: -0.02em;
```

Then rewrite the three band variants onto the value ramp, keeping the class names so no TSX changes:

```css
.score--green {
  background: var(--surface);
  color: var(--seq-600);
  box-shadow: inset 0 0 0 1.5px var(--seq-600);
}
.score--amber {
  background: var(--surface);
  color: var(--seq-450);
  box-shadow: inset 0 0 0 1.5px var(--seq-450);
}
.score--red {
  background: var(--surface);
  color: var(--seq-250);
  box-shadow: inset 0 0 0 1.5px var(--seq-250);
}
```

A high score is now black and heavy, a low score light grey — the same ranking information carried by value instead of hue, which is also the one encoding that survives being photocopied or read by someone colour-blind.

Note `html[data-density='compact'] .score` at line 1162 overrides `font-size`. Update its value to `1.05rem` so compact stays proportionally smaller.

- [ ] **Step 4: Give every other number tabular figures**

Add `font-variant-numeric: tabular-nums;` to the rules for the tile counts, the tab counts, and the money/deadline column. Numbers in a column that do not align are the single most common way a data UI looks amateur.

- [ ] **Step 5: Look at it**

Re-run the screenshot from Task 3 Step 3. Check: the score reads as the entry point of its row; dates and reference numbers are visibly mono; body prose is unchanged; no column has ragged digits.

- [ ] **Step 6: Test and commit**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: PASS, no type errors.

```bash
git add frontend/src/styles.css
git commit -m "Tokenise the type stacks and make the score the row's anchor"
```

---

### Task 5: Verify the phase

**Files:** none modified.

- [ ] **Step 1: Full suite, both halves**

```bash
cd frontend && npx vitest run && npx tsc --noEmit
cd ../backend && ./.venv/bin/python -m pytest -q
```

Expected: frontend green, no type errors, backend unchanged. Two pre-existing failures in `tests/test_scheduler_jobs.py` are unrelated — they read the real `data/tenders.db`, which holds `app_settings.scheduler.enabled='true'`. Do not fix them here.

- [ ] **Step 2: Screenshot all three widths**

Run the driver at 1512×950, 1024×800, and 390×844. Confirm at each: white ground, black actions, colour only on status, no layout change versus the pre-phase screenshots in the scratchpad.

- [ ] **Step 3: Confirm the phase's own constraint held**

Run: `git diff --stat main -- frontend/src`
Expected: changes confined to `styles.css`, `preferences.ts`, `preferences.test.ts`, `types/index.ts`, `Masthead.tsx`, `Dashboard.tsx`. Any other file means layout work leaked in — revert it into Phase 2.
