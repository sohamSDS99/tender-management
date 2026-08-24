import { describe, expect, it } from 'vitest';
import {
  DEFAULT_FILTERS,
  activeChips,
  activeFilterCount,
  filtersFromSearch,
  searchFromFilters,
} from './urlFilters';
import type { TenderFilters } from '../types';

/**
 * This codec is load-bearing for the Slack digest.
 *
 * `notifier.digest_permalink` emits `?minimum_score=<n>&active_only=true&sort=first_seen_desc`
 * and every tender entry emits `?tender=<id>`. If this file stops understanding
 * those parameter names, every link in every digest silently lands on an
 * unfiltered dashboard, which is the one defect a demo audience would notice.
 */

const LABELS = {
  fit: (v: string) => v,
  deployment: (v: string) => v,
  source: (v: string) => v,
  category: (v: string) => v,
};

describe('the contract with the Slack digest', () => {
  it('understands the exact query the digest links to', () => {
    const { filters } = filtersFromSearch(
      '?minimum_score=70&active_only=true&sort=first_seen_desc',
    );
    expect(filters.minimum_score).toBe(70);
    expect(filters.active_only).toBe(true);
    expect(filters.sort).toBe('first_seen_desc');
  });

  it('reads the tender deep link Dashboard.tsx has always supported', () => {
    expect(filtersFromSearch('?tender=4321').tenderId).toBe(4321);
    expect(filtersFromSearch('?tender=abc').tenderId).toBeNull();
    expect(filtersFromSearch('?tender=-5').tenderId).toBeNull();
    expect(filtersFromSearch('').tenderId).toBeNull();
  });

  it('keeps the tender open alongside a filter set', () => {
    const { filters, tenderId } = filtersFromSearch('?minimum_score=0&query=SDS&tender=8');
    expect(tenderId).toBe(8);
    expect(filters.minimum_score).toBe(0);
    expect(filters.query).toBe('SDS');
  });
});

describe('round-tripping', () => {
  it('restores a fully populated filter set unchanged', () => {
    const filters: TenderFilters = {
      query: 'safety data sheets',
      minimum_score: 35,
      maximum_score: 90,
      sources: ['ted', 'find_a_tender'],
      countries: ['DEU', 'NOR'],
      categories: ['sds_management'],
      statuses: ['open'],
      fit_statuses: ['high_fit', 'manual_review'],
      first_seen_from: '2026-08-21T06:00:00Z',
      deployment_fits: ['cloud_required'],
      deadline_from: '2026-08-01',
      deadline_to: '2026-09-30',
      published_from: '2026-07-01',
      published_to: '2026-08-21',
      active_only: false,
      has_deadline: true,
      sort: 'deadline_asc',
      page: 3,
      page_size: 50,
    };
    const restored = filtersFromSearch(`?${searchFromFilters(filters, 99)}`);
    expect(restored.filters).toEqual(filters);
    expect(restored.tenderId).toBe(99);
  });

  it('an empty query string yields the default view', () => {
    expect(filtersFromSearch('').filters).toEqual(DEFAULT_FILTERS);
  });

  it('writes nothing for a default view, so a shared URL stays clean', () => {
    expect(searchFromFilters(DEFAULT_FILTERS, null)).toBe('');
  });

  it('survives a second round trip (no drift)', () => {
    const once = filtersFromSearch('?minimum_score=10&sources=ted&page=2').filters;
    const twice = filtersFromSearch(`?${searchFromFilters(once, null)}`).filters;
    expect(twice).toEqual(once);
  });
});

describe('hostile and careless URLs', () => {
  it('swaps an inverted score pair instead of returning nothing', () => {
    const { filters } = filtersFromSearch('?minimum_score=90&maximum_score=20');
    expect(filters.minimum_score).toBe(20);
    expect(filters.maximum_score).toBe(90);
  });

  it('clamps scores to 0..100', () => {
    expect(filtersFromSearch('?minimum_score=-40').filters.minimum_score).toBe(0);
    expect(filtersFromSearch('?maximum_score=9999').filters.maximum_score).toBe(100);
  });

  it('falls back on a non-numeric score', () => {
    expect(filtersFromSearch('?minimum_score=NaN').filters.minimum_score).toBe(
      DEFAULT_FILTERS.minimum_score,
    );
    expect(filtersFromSearch('?minimum_score=').filters.minimum_score).toBe(0);
  });

  it('drops enum values the API would reject, rather than sending a 422', () => {
    const { filters } = filtersFromSearch(
      '?fit_statuses=high_fit&fit_statuses=DROP+TABLE&deployment_fits=nonsense',
    );
    expect(filters.fit_statuses).toEqual(['high_fit']);
    expect(filters.deployment_fits).toEqual([]);
  });

  it('ignores a malformed sort', () => {
    expect(filtersFromSearch('?sort=../../etc/passwd').filters.sort).toBe(DEFAULT_FILTERS.sort);
  });

  it('ignores malformed dates', () => {
    const { filters } = filtersFromSearch('?deadline_from=21-08-2026&deadline_to=2026-09-30');
    expect(filters.deadline_from).toBe('');
    expect(filters.deadline_to).toBe('2026-09-30');
  });

  it('ignores an unsupported page size and a bad page number', () => {
    expect(filtersFromSearch('?page_size=7').filters.page_size).toBe(DEFAULT_FILTERS.page_size);
    expect(filtersFromSearch('?page=0').filters.page).toBe(1);
    expect(filtersFromSearch('?page=-3').filters.page).toBe(1);
  });

  it('treats has_deadline as a tri-state', () => {
    expect(filtersFromSearch('?has_deadline=true').filters.has_deadline).toBe(true);
    expect(filtersFromSearch('?has_deadline=false').filters.has_deadline).toBe(false);
    expect(filtersFromSearch('?has_deadline=maybe').filters.has_deadline).toBeNull();
    expect(filtersFromSearch('').filters.has_deadline).toBeNull();
  });

  it('keeps repeated list parameters, matching the API', () => {
    expect(filtersFromSearch('?sources=ted&sources=sam&sources=').filters.sources).toEqual([
      'ted',
      'sam',
    ]);
  });
});

describe('chips explain why the result set looks the way it does', () => {
  it('shows the default narrowing rather than hiding it', () => {
    // 320 stored tenders becoming 9 must never look unexplained.
    const chips = activeChips(DEFAULT_FILTERS, LABELS).map((c) => c.key);
    expect(chips).toContain('minimum_score');
    expect(chips).toContain('active_only');
    expect(activeFilterCount(DEFAULT_FILTERS)).toBe(2);
  });

  it('an unfiltered view has no chips', () => {
    const open: TenderFilters = { ...DEFAULT_FILTERS, minimum_score: 0, active_only: false };
    expect(activeChips(open, LABELS)).toEqual([]);
    expect(activeFilterCount(open)).toBe(0);
  });

  it('each chip clears exactly its own filter', () => {
    const filters: TenderFilters = {
      ...DEFAULT_FILTERS,
      sources: ['ted'],
      fit_statuses: ['high_fit'],
      query: 'sds',
    };
    const chips = activeChips(filters, LABELS);
    const sourceChip = chips.find((c) => c.key === 'sources');
    expect(sourceChip?.clear).toEqual({ sources: [] });
    const applied = { ...filters, ...sourceChip?.clear };
    expect(applied.sources).toEqual([]);
    expect(applied.fit_statuses).toEqual(['high_fit']);
    expect(applied.query).toBe('sds');
  });

  it('clearing every chip reaches a genuinely unfiltered view', () => {
    let filters: TenderFilters = {
      ...DEFAULT_FILTERS,
      sources: ['ted'],
      countries: ['DEU'],
      has_deadline: true,
      deadline_to: '2026-09-01',
    };
    for (let i = 0; i < 12 && activeChips(filters, LABELS).length; i += 1) {
      filters = { ...filters, ...activeChips(filters, LABELS)[0].clear };
    }
    expect(activeChips(filters, LABELS)).toEqual([]);
  });
});
