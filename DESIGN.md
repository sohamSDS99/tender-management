# Design

<!-- impeccable:design-schema 1 -->

## The world: an instrument panel, dark by default

This world replaces the previous one ("a quiet document, not a dashboard"). That
was a defensible interface and it is no longer the brief: a reviewed mockup was
supplied and adopted wholesale, dark-themed, and the earlier document world is now
the anti-reference in exactly the way its own predecessor was. Two of its explicit
refusals — metric tiles, and cards as page structure — are load-bearing here.

The shift is honest about what the product became. It is no longer only a reading
surface for discarding notices; it is also the console that runs the collector. You
can start a sweep, re-score everything, pause the automation and move its times
from this page. A console needs enclosed, addressable regions, so cards are back —
but as *panels with a job*, never as decoration around a paragraph.

Mode: **Operate**. Scanability, consistency and the real usage scene outrank
expression. Brand lives in the precision of the details.

**Principles for this surface**

1. Every number is a filter. A count you cannot click is a count you cannot check.
2. A tab's count must equal the list it opens. This is the reason the score buckets
   filter on score alone (D23 notes; `views.ts`).
3. Colour is meaning. Status colour never appears without a word beside it.
4. One piece of chrome never moves. Everything else may collapse, slide or scroll;
   the rail does not, which is what makes Settings findable at all.
5. Nothing is discarded, and the page says so where it could be doubted.

## Palette

Dark is the default and therefore lives on bare `:root`; light is the
`html[data-theme="light"]` override. That order matters: a failure to stamp
`data-theme` lands on the intended theme instead of flashing the other one.

```
            dark (default)      light
--page      #0b1015             #f4f6f9    app ground
--surface   #131a21             #ffffff    panels, cards, rows
--surface-2 #182129             #f8fafc    recessed: footers, formula, raw
--surface-3 #1d2731             #eff3f8    pressed, track, tag, seg ground
--line      #242f3a             #e2e7ee    hairlines
--line-str  #33404d             #cfd7e0    input borders, dividers that must read

--ink       #eef3f8             #0d1b26    titles, values
--ink-soft  #9dabb9             #5a6b7c    body
--ink-muted #8b99a7             #64707c    meta, labels, placeholders

--brand     #7cb3e8             #0f4c81    selection, focus, links, primary
--brand-ink #0b1015             #ffffff
--brand-soft#12283c             #e8f0f8
```

Semantic pairs are an ink on a tint of the same hue, so secondary text is never
grey-on-colour. Status marks (`--mark-*`) are for dots and pips only, where a word
always sits beside them.

```
--good  ink #5fd39b  tint #123024    strong fit, healthy source
--warn  ink #f4c04f  tint #3a2c0c    review, closing soon
--bad   ink #f28b8b  tint #3a1717    disqualified, failed, unavailable
--blue  ink #8cc0f0  tint #12283c    new, informational
```

A separate sequential blue ramp (`--seq-100..600`) carries *magnitude only*, on the
detail panel's score meters. It is deliberately not the semantic scale: a subscore
of 40 is not "bad", it is small.

Measured, not judged by eye: every text/ground pair in use is ≥ 4.5:1 in both
themes (light 5.06–16.1, dark 6.0–17.1).

## Type

Inter, with a full system fallback stack — the host is on an internal network and
may have no route to Google Fonts, in which case the page must still be correctly
typeset rather than unstyled.

```
stack   'Inter', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif
base    15px / 1.5
weights 400 body · 500–550 labels · 600–680 titles, values   (no more)
track   -0.011em on headings; -0.025em on the large tile numerals
figures font-variant-numeric: tabular-nums on every score, count, value and date
```

Card titles clamp to two lines (one in compact density) and are the loudest thing
in a card. Nothing else in a card exceeds 0.875rem.

## Space and layout

```
rail    62px, fixed, full height — permanent
slideout 340px, fixed beside the rail, slides on translateX
shell   max-width 1440px, padded past the rail
radius  12 / 8 / 6px
rhythm  4 6 8 10 12 14 18 24
```

A **permanent 62px rail** holds the product mark at the top and, pinned to the
bottom by a flex gap, the **Settings** tab carrying a badge of how many filters
are active. Clicking it slides the settings panel out from behind the rail.

Everything else is one column inside the shell: **topbar** → **tiles** →
**sources** → **toolbar** → **results** → **pager** → **runs**.

The slide-out carries **no blocking scrim** on purpose. It covers the left of the
page, but the results stay visible and clickable to its right, so a filter can be
watched taking effect while it is still being set — which is the whole reason to
open it. Escape closes it, as does the tab it came from. While shut it is
`visibility: hidden` (delayed to the end of the slide), because a panel merely
translated off-screen still holds its tab stops.

The toolbar is sticky: scrolling a long list away from its own controls is what
makes a results page feel like a document instead of a tool.

## Elevation

Three shadows, declared once. `--shadow-1` on resting panels, `--shadow-2` on
hover, `--shadow-3` on the one thing that genuinely floats — the detail drawer.
Cards carry a hairline *and* `--shadow-1`, which the previous world forbade; in an
enclosed-panel design the hairline defines the edge and the shadow separates the
panel from the ground, and they are doing different jobs.

## Components

- **Stat tile.** Dot + uppercase label · large tabular numeral · one-line
  substatement · `Filter →` on hover. Pressed state is a brand border plus ring.
- **Result card.** `58px | 1fr | 168px`. Score chip left, title + badges + meta +
  one reason line centre, deadline/value/link right. The title is the real button
  and its `::after` overlays the card as the hit area, so the external link keeps
  its own tab stop. Never a div with an onClick.
- **Score chip.** 52px rounded square, tabular, tinted by band with a 1.5px inset
  ring. Compact drops it to 42px.
- **Badge.** Text on a semantic tint, pill, no border except `--line` variant, icon
  only where the words alone are ambiguous.
- **Tabs.** Text with a 2px underline, a dot for tone, and a count pill — where the
  count is provably the same population the tab filters.
- **Chips / checks / segs / switches** for Settings. One shape family, brand fill
  when on.
- **Buttons.** 8px radius; primary, default, ghost, danger, icon, sm.
- **States.** Skeleton, empty (naming which of three situations it is), error
  (naming the command that fixes it). Every interactive element ships default,
  hover, focus-visible, active, disabled.

## Motion

One authored moment: the detail drawer entering, 340ms on
`cubic-bezier(.32,.72,0,1)`. Everything else is ≤ 200ms on colour, border,
transform and shadow. No layout properties are animated — no `width`, `height`,
`padding` or `margin` transitions anywhere. `prefers-reduced-motion` removes
transforms and collapses durations.

## Browser surfaces

Themed from the palette rather than left at browser default: `color-scheme`, text
selection, scrollbars (both `scrollbar-color` and the WebKit pseudo-elements),
`accent-color` on ranges, checkboxes and date inputs, and tabular numerals in every
data column.

## Refused for this surface

Gradients as decoration (the one gradient is the 40px logo mark). Glassmorphism.
Emoji or Unicode glyphs as icons — every icon is a drawn SVG at an optically
constant 1.5px stroke. Coloured left borders **except** on the source cards, where
the 3px edge encodes connector status beside a text status label and is doing work;
the mechanical detector flags this pattern and it is kept deliberately, from the
mockup. Counts on the fit / deployment / capability chips, because those come from
unfiltered `/api/stats` and inside a narrowed view promise results that are not
there. Monospace as a costume — it appears only on source keys, references and raw
payloads, which are genuinely code. A modal for anything but the detail drawer —
this refusal was broken once, by the sign-in dialog added with accounts, and the
replacement is a full page (D26). If a surface feels like it wants a modal, that
is usually a sign it deserves the whole width instead.

## Marking a notice not relevant (D27)

The first control on this surface that removes something from view, which makes
principle 5 — *nothing is discarded, and the page says so where it could be
doubted* — the binding constraint rather than a nicety.

**One button on the card, both in the drawer.** Rejecting is the frequent act; it
is what working through a list *is*. Keeping something is rare and deliberate and
carries a note, so it lives in the detail panel. A pair of buttons on all
twenty-five rows would make a results page look like a form.

**The reject control is quiet, and coloured only on hover.** It appears on every
row, and a column of red buttons reads as an error state — colour is meaning
here, so a control that is merely available must not spend any.

**Four places say so, because one silent removal would undo the trust:**

* a chip in the toolbar on the default view, "hiding what was marked not
  relevant", alongside the score floor and open-only chips;
* the **Not relevant** lens in the rail, with its own count, clickable like every
  other number;
* a badge on the card naming *which* hid it — a reviewer's decision and a
  machine's guess at one are different things, and only one of them is evidence;
* the sentence after a mark, which states how many *other* notices it hid. One
  click can remove a dozen rows; unannounced, that reads as the list breaking.

**A machine hide always shows its reason, in words, on the card.** "Hidden
because 'laboratory furniture' appears in 6 notices you marked not relevant and
only 1 other." The full list is in the drawer and the whole model, with its
evidence, is a table under Matching rules. Nothing here is allowed to be opaque:
the phrase lists elsewhere on that page can be argued with by reading them, and a
derived pattern earns the same standing only by showing its counts.

**Nothing new was refused, and nothing previously refused was taken up.** No
modal confirms a mark — it is reversible, and a confirmation for a reversible act
is a tax on the frequent case. No icon-only button, no emoji, no new colour.
