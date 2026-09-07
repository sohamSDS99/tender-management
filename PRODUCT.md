# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary — the bid team, daily.** People whose job is finding public-sector work
worth bidding on. They open the dashboard to answer one question: *what came in
that is worth my time today?* They are not analysts and not engineers; they judge
an opportunity in seconds from the buyer, the deadline, the money and the reason
it was flagged, then leave to act on it elsewhere.

**Secondary — leadership, occasionally.** Checks the pipeline is healthy and that
nothing was missed. Cares about volume and whether the system is actually running.
Confirmed as a glance, not a workflow: the overview stays available but must not
compete with the daily hunt.

All users are internal staff on the company network. There are no accounts, no
roles and no per-user state, by decision (`docs/DECISIONS.md` D13).

## Product Purpose

Public bodies publish far more procurement notices than anyone can read, across
dozens of unrelated portals in several languages. Tender Monitor watches nine of
those sources on a fixed schedule, normalises every notice into one shape, scores
it for relevance to this company's products, and surfaces the small number worth a
human's attention — with the reasoning attached.

Success is a bidder opening it once a day, spending under a minute, and leaving
with the one or two notices that matter. Failure is them going back to reading
portals by hand, or losing trust in the score.

## Positioning

Not a keyword alert. Two things a neighbouring tool could not truthfully copy:

1. **The score explains itself.** Every notice carries three weighted subscores
   (`0.55 × topic + 0.30 × product fit + 0.15 × procurement intent`), the plain
   sentences that produced them, and any cap that overrode them. Deterministic —
   the same notice always scores the same, with no model in the loop.
2. **It judges deployment fit, not just topic.** A notice can be a perfect
   subject-matter match and still be worthless because the buyer mandates
   on-premises installation. The engine detects that and caps the score, with the
   quoted phrase that triggered it.

## Operating Context

- Runs on a machine inside the company network; reached at a LAN address.
- Fetches automatically at **00:00 and 12:00 Asia/Dhaka**, and those times are
  editable from the dashboard (D19), as is whether it runs at all (D21).
- A sweep and a full re-score **can** be started from the interface, by anyone on
  the network, without a secret. Requested directly, and it reversed the earlier
  position that fetching must be untriggerable. The controls that stop it being
  abused are server-side and are not authentication: one sweep at a time, and a
  cooldown between operator-initiated runs (D23).
- A Slack digest announces newly-found notices scoring 70+, each linking straight
  into this dashboard's detail view for that notice.
- Typical arrival pattern: a couple of hundred notices ingested per sweep, of
  which only a handful clear the relevance bar. The interface's real job is
  discarding, not displaying.
- Users act **outside** the app: they open the buyer's original notice, or discuss
  it in Slack. Confirmed: the dashboard does not need to track status, assign
  owners, or record decisions.

## Capabilities and Constraints

**Confirmed capabilities.** Browse, search, filter and sort stored notices; open
one and read its full record including the score reasoning, key facts,
classification codes, description, documents and raw source payload; see which
sources are healthy and when the next automated run is; share any view by URL;
mark a notice relevant or not relevant, and have the system learn from it.

**Still no per-notice editing — but there is now a decision to record (D27).**
This supersedes the earlier "no decision to record" and nothing else in this
section. A reviewer can mark a notice **relevant** or **not relevant**, which
hides it from the working views and teaches the system to hide notices matching
the same patterns. The distinction that matters, and which the interface must
keep visible:

* **The notice itself is still never edited.** A verdict is a separate record
  about the notice, not a field on it, so every sweep and re-score leaves it
  untouched. There is still no status to set and no owner to assign.
* **A verdict cannot change a relevance score.** It decides only whether a
  notice is *shown*. The score remains the scoring engine's, computed from the
  phrase file alone.
* **Nothing is discarded, and every mark is reversible.** Hidden notices live in
  the **Not relevant** lens with the reason each was hidden, and withdrawing a
  mark re-derives the patterns from what is left.

What a user can trigger beyond that are the two whole-system operations — start a
sweep, re-score everything — and the sweep schedule. Both sweep and re-score are
additive or deterministic: a sweep upserts on `(source, notice id)`, and a
re-score recomputes a pure function of data already stored, so neither can
destroy anything (D23).

**Terminology used throughout** (and which the interface should not rename):
tender / notice, buyer, deadline, relevance score, fit status, deployment fit,
capability, disqualifier, review flag, source, sweep / fetch run, verdict
(relevant / not relevant), learned pattern, hidden.

**Technical constraints.** React 18 + TypeScript + Vite, plain CSS. Dependencies
are `react` and `react-dom` only; adding one requires justification in
`docs/DECISIONS.md`. Served by nginx on the same origin as the API.

**Devices.** Desktop only — confirmed. Phone use is explicitly out of scope, which
supersedes the earlier "responsive to ~360px" requirement. A narrow window must
still degrade sanely rather than break, but no design effort is spent there.

## Brand Commitments

No logo, wordmark, brand palette or typeface has been supplied. The product name
is "Tender Monitor"; the company is SDS Manager. The only binding visual
constraint the user has stated is direction, recorded here verbatim because it was
volunteered: **simple, calm, aesthetically pleasing, user-friendly.** The previous
interface was rejected as "extremely bad, not looking good at all", so the
incumbent look is anti-reference, not a starting point.

## Evidence on Hand

- Real ingested data: ~283 notices from live government sources in the running
  database. Enough to design against truthfully.
- 14 committed seed fixtures (`backend/tests/fixtures/seed_tenders.json`) spanning
  every score band, deployment class and known false-positive case — including a
  notice where "SDS" means software-defined storage, and one capped for mandatory
  on-premises hosting. These are the only sanctioned sample data.
- Eight real sources: EU TED, US SAM.gov, UK Find a Tender, UK Contracts Finder,
  World Bank, CanadaBuys, AusTender, Brazil PNCP, HigherGov.
- No testimonials, customers, pricing or benchmarks exist. Future work must not
  invent any.

## Product Principles

1. **Discarding is the job.** Hundreds arrive; a handful matter. Every design
   decision should help a bidder reject faster, not display more.
2. **A score with no reason is worthless.** Trust comes from the reasoning being
   visible, not from the number being confident.
3. **Say what is happening to the data.** Automation the user cannot trigger must
   be automation the user can always see: when it last ran, what it found, what
   broke.
4. **Never imply an action that does not exist.** The tool finds and explains;
   it does not track, assign or submit. No control should suggest otherwise.
5. **Built for someone who is not technical and is in a hurry.** Plain words over
   jargon, one obvious path, nothing that needs explaining.

## Accessibility & Inclusion

No formal standard was set for this internal tool. Baseline expectations still
apply and were built into the previous version: full keyboard operation, visible
focus, adequate contrast in both light and dark themes, and status never carried
by colour alone.
