# User-defined sources

**Date:** 2026-08-25
**Status:** approved design, not yet planned

## 1. Why

`connectors/registry.py` hardcodes eight Python classes. Adding a ninth means
writing a module and shipping a release, which puts "we found a new
procurement feed" on the engineering backlog rather than in the hands of the
person who found it.

The goal stated plainly: *if we find a new provider with API access, add it
from the dashboard.*

## 2. What makes this possible

`connectors/ocds.py::normalize_release()` is **schema-driven, not
portal-driven**. It takes a raw OCDS release and returns a `NormalizedTender`;
UK Find a Tender and UK Contracts Finder both just feed it. The per-portal code
around it is only a URL, a pagination style, and auth.

So the reusable part of a connector already exists. What is portal-specific is
*fetching* and *field naming* — and both can be data instead of code.

## 3. The contract a source must satisfy

Whatever route is taken, the output is a `NormalizedTender`
(`connectors/base.py:86`). Five fields are load-bearing:

| Field | Required | Why |
|---|---|---|
| `source_notice_id` | yes | The dedupe key, with `source`. Without it every sweep re-inserts everything. |
| `title` | yes | Scored, displayed, and the main match surface. |
| `description` | no, but | Most of the relevance signal is here. A source without it will score poorly and look broken. |
| `deadline` | no | Drives Closing soon and the digest urgency. Absent is honest — AusTender has none. |
| `source_url` | yes | "Original notice" is how anyone acts on a result. |

`content_hash` is computed from these, so a mapping that omits `title` makes
every notice look changed on every sweep.

## 4. Two routes, chosen by a probe

### 4a. Known standard — zero configuration

The probe fetches one page and inspects it:

- **OCDS** — a `releases` array whose entries carry `ocid`. Parsed by the
  existing `normalize_release()`. No mapping asked for.
- **RSS / Atom** — an `<rss>` or `<feed>` root. Parsed the way
  `austender.py` already does.

This covers the common case with parsing that is already tested.

### 4b. Anything else — map the fields once

The probe reports the paths it found in the payload with a sample value for
each, and the operator points them at the contract in §3:

```
title        ->  notice.subject           "Supply of laboratory chemicals"
description  ->  notice.longDescription   "The authority seeks…"
deadline     ->  dates.closing            "2026-09-30T17:00:00Z"
buyer        ->  organisation.legalName   "Ministry of Health"
url          ->  links.self               "https://…/notice/44812"
```

Paths are dotted with `[]` for arrays (`data.items[].tender.title`). This is
what makes the feature work with a portal nobody anticipated, which is the
actual requirement.

Dates are parsed with the existing `base.parse_datetime`, which already
handles the formats the eight built-ins encounter, and the mapping stores the
format only when auto-detection fails.

## 5. The probe reports what parsed, not what answered

A 200 proves the credential works. It says nothing about whether the response
is readable, and a source that answers but yields nothing is precisely the
failure this system just spent a day fixing on SAM.gov.

So the probe returns:

```
200 OK · 47 records found · 44 parsed · 6 match your profile
```

and refuses to save on `0 parsed`. Three outcomes, each said plainly:

| Outcome | Message |
|---|---|
| Parsed | The counts above, then Save becomes available. |
| 200, unrecognised shape | "The endpoint answered, but nothing in the response looks like a notice. Map the fields below, or check the URL." |
| Not 200 | The status and the first 200 characters of the body, so a bad key reads differently from a bad URL. |

## 6. Storage

A `sources` table. The eight built-ins stay as code and are **not** migrated
into it — they have behaviour no configuration expresses (PNCP's
`modalidades`, CanadaBuys' dual feed, TED's expert-search syntax), and
rewriting them as data would be a large change with no user-visible benefit.

```
sources
  name            text primary key      slug, unique against CONNECTOR_CLASSES
  display_name    text
  homepage        text
  url             text                  the endpoint to fetch
  auth            text                  'none' | 'query' | 'header' | 'bearer'
  auth_param      text                  e.g. 'api_key' or 'X-Api-Key'
  format          text                  'ocds' | 'rss' | 'json'
  mapping         json                  null for ocds/rss
  pagination      json                  null, or {style, param, size}
  enabled         boolean
  created_at      timestamp
```

The credential itself does **not** live here. It goes in `app_settings` under
the existing `source.{name}.credential` key, so there is one write-only
credential path rather than two, and §7's guarantees apply unchanged.

`registry.build_all()` returns the built-ins plus one `GenericConnector` per
enabled row.

## 7. Security — the part not asked for

This lets a dashboard user make the **server** fetch a URL they type. The
dashboard is unauthenticated by design (D23), and that reasoning held because
the writes were expensive-not-confidential. A server-side fetcher pointed at an
arbitrary address is a different class of thing: it can reach the internal
network, cloud metadata endpoints, and `localhost`.

Required, and not optional:

- **https only.** No `http`, no `file`, no other scheme.
- **Public addresses only.** Resolve the host and refuse loopback, link-local,
  and RFC1918 ranges. Re-check after every redirect, because a public host can
  redirect to `169.254.169.254`.
- **At most 3 redirects**, and the response capped by the existing
  `MAX_RESPONSE_BYTES`.
- **Gated by `ALLOW_OPERATOR_ACTIONS`**, like every other operator write.

This is roughly thirty lines and it covers the realistic risk. Without it the
feature is a hole, so it ships with the feature or the feature does not ship.

## 8. What stays impossible

Stated in the UI rather than discovered by the operator:

- **No API** — HTML-only portals need scraping, which is a connector.
- **OAuth or a login flow** — only a static key or header is supported.
- **Binary or proprietary formats.**

The probe names which of these it hit rather than failing vaguely.

## 9. Phasing

1. **Sources as data.** The table, `GenericConnector`, registry returning
   built-ins plus rows. No UI. Nothing user-visible; entirely testable.
2. **Probe and auto-detect.** The probe endpoint, the SSRF guard from §7, OCDS
   and RSS detection, and the Add-source UI for the zero-config case.
3. **Field mapping.** The path inspector and the mapping form — the fully
   flexible route.

Phase 2 likely covers most real portals. Phase 3 is what makes the feature
open-ended, which is the stated requirement, so it is not optional — only last.

## 10. Testing

- `GenericConnector` against recorded payloads for each format, including a
  payload that parses to zero records.
- Mapping: a missing required field is refused; a path into an array works; a
  date in three formats parses.
- SSRF: `http://`, `localhost`, `127.0.0.1`, `10.0.0.1`, `169.254.169.254`,
  and a public host redirecting to a private one are each refused.
- The probe never saves anything; a refused save leaves no row.
- A user-added source appears in `enabled_sources()` and takes part in a sweep.

## 11. Open

- Whether a user-added source can be edited after creation or only replaced.
  Editing a mapping changes what `content_hash` produces, which makes every
  notice from that source look changed once.
- Rate limiting per source. The built-ins have hand-tuned page caps; a
  user-added source has none.
