# Design

<!-- impeccable:design-schema 1 -->

## The world: a quiet document, not a dashboard

The job is **discarding**. Two hundred notices arrive; two matter. The interface
that serves that is a calm, reading-shaped page — closer to a well-set report than
to an admin console. Content sits directly on the page. Hairlines separate; boxes
do not enclose. Typography carries hierarchy so containers do not have to.

The rejected predecessor is the anti-reference: it stacked a header card, five
metric cards, a source card and a toolbar card above the first tender, spent its
largest type on the least useful number, and drew a border *and* a shadow around
everything. None of that returns.

**Principles for this surface**

1. The list is the page. It begins in the first viewport, not below four panels.
2. One surface. The page colour *is* the card colour; separation is a 1px rule.
3. Type ranks things, not chrome. Exactly one element per row may be loud.
4. Colour is reserved for meaning. Nothing is tinted for decoration.
5. Automation is a sentence, not a widget.

## Palette

Light is the default: daytime office, desktop, long sessions, low glare. A warm
paper white rather than clinical `#fff` — it sits under fluorescent light without
glare and makes the ink feel set rather than rendered.

```
--paper        #fcfbf9   page and row surface (warm white)
--paper-2      #f6f4f1   recessed: search field, table head, code
--paper-3      #efece7   pressed / selected row
--rule         #e4e0da   hairlines (the only separator)
--rule-strong  #cfc9c0   input borders, dividers that must read

--ink          #17191d   headings, titles, numbers
--ink-2        #4c5259   body, labels
--ink-3        #7b8189   meta, captions, placeholders

--accent       #3d4f9b   selection, focus, links, primary action
--accent-ink   #ffffff
--accent-soft  #edeff8   selected row wash
```

Semantic colours are deliberately **not** the accent, so a selected row can never
be confused with a good score. Each is an ink on a tint, both derived from the
same hue so secondary text is never grey-on-colour:

```
--good  ink #1c6047  tint #e8f2ee   strong fit, healthy source
--warn  ink #7d5410  tint #f7efe0   needs review, closing soon
--bad   ink #93302f  tint #f8eae9   disqualified, failed, closed
```

Dark theme keeps the same roles and hue relationships on a near-black that is
warm rather than blue, so the two themes feel like one product.

## Type

One family. `operate.md` permits a familiar sans for product UI, and it keeps the
dependency count at zero — but it is tuned, not defaulted: explicit tracking per
step, tabular figures wherever numbers align, and a hard cap on how many weights
appear.

```
stack   ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif
scale   0.75  0.8125  0.875  1  1.0625  1.25  1.5rem      (fixed, ratio ~1.15)
weights 400 body · 500 labels/meta · 600 titles and numbers   (three, no more)
track   -0.011em at 1rem and above; 0 below; never past -0.03em
figures font-variant-numeric: tabular-nums on every score, value, date and count
```

Titles are the loudest thing in a row and clamp to two lines. Nothing else in a
row exceeds 0.875rem.

## Space and layout

```
grid    single column, max-width 1080px, 32px gutters
rhythm  4 8 12 16 24 32 48 64
row     20px vertical padding, 24px column gap
```

More space above a heading than below it. Groups are tight internally and
separated generously — that, not borders, is what makes regions read as regions.

Structure, top to bottom:

1. **Header**, one line: product name, the automation sentence, theme toggle.
2. **Views**, a text segmented control — *Needs attention · New · Closing soon ·
   All* — replacing the five metric tiles. These are the questions a bidder
   actually arrives with.
3. **Toolbar**: search, sort, and a `Filters` disclosure.
4. **Filters**, inline and collapsed by default. Not a drawer and not a modal:
   `operate.md` treats a modal as laziness for a task needing neither
   interruption nor protected focus.
5. **The list**. Hairline-separated rows.
6. **Detail**, a right-hand panel over a scrim. This one earns its overlay: it is
   detail-in-context, deep-linked by `?tender=`, and the Slack digest opens it
   directly.

## Elevation

Declared once, and almost never. Rows and regions use a hairline. Exactly one
thing genuinely floats — the detail panel — and its shadow carries both an offset
and a blur:

```
--shadow-panel  -8px 0 32px -12px rgba(20,22,26,.18)
```

No element ever has both a visible border and a shadow.

## Components

- **Row.** Score (small, tabular, left) · title + one meta line · deadline and
  value right-aligned. Hover lifts the surface a shade; selection washes it with
  `--accent-soft` and shows a 2px accent edge. No card.
- **Score.** A two-digit numeral at 1.0625rem/600 in tabular figures, over a
  4px-tall bar tinted by band. Small, readable, not a badge.
- **Badge.** Text on a semantic tint, 2px radius, no border, no icon unless the
  meaning is not in the words.
- **Buttons.** One shape (6px radius), three variants: primary (accent), quiet
  (transparent, ink-2), and icon-only (32px, square). Pills only for the view
  control.
- **Fields.** `--paper-2` ground, 1px `--rule-strong`, accent ring on focus.
- **States.** Every interactive element ships default, hover, focus-visible,
  active, disabled. Lists ship skeleton, empty and error; the empty state names
  which filter to clear, and the error state names the command that fixes it.

## Motion

One authored moment: the detail panel entering. 220ms, exponential ease-out, from
an already-visible default. Everything else is 150ms on colour and background
only. No page-load choreography — the product loads into a task.

```
--ease  cubic-bezier(.22,.61,.36,1)
```

`prefers-reduced-motion` removes transforms and keeps opacity.

## Browser surfaces

The parts not drawn still carry the design, and shipping them at browser default
is the clearest sign a page was assembled rather than built. Themed from the
palette: text selection, caret, focus ring, scrollbar, `accent-color` for native
controls, `color-scheme`, and tabular numerals in every data column.

## Refused for this surface

Metric tiles. Cards as page structure. Any nested card. Traffic-light dots used
decoratively. Unicode glyphs as icons — every icon is drawn SVG at a single 1.5px
stroke. Monospace as a costume for "technical"; it appears only on identifiers and
raw payloads, which are genuinely code. Gradient text. Coloured left borders wider
than 1px. A modal for anything that is not the detail panel.
