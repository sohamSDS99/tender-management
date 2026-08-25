import { describe, expect, it } from 'vitest';
import {
  PAGE_KEYS,
  SETTINGS_CATEGORIES,
  categoryFor,
  settingsFromSearch,
  withSettings,
} from './settingsNav';

describe('the settings menu', () => {
  it('offers every category the old panel held, and nothing invented', () => {
    expect(SETTINGS_CATEGORIES.map((c) => c.key)).toEqual([
      'filters',
      'rules',
      'display',
      'automation',
      'sources',
      'system',
    ]);
  });

  it('keeps filters on the live panel and everything else on a page', () => {
    // Filtering is the most frequent action here, and the panel carries no scrim
    // so the list stays visible while a filter is being set. A full-width page
    // would hide the very results being narrowed.
    expect(categoryFor('filters')?.surface).toBe('panel');
    for (const key of ['display', 'automation', 'sources', 'system']) {
      expect(categoryFor(key)?.surface).toBe('page');
    }
  });

  it('gives every category a label and a blurb, so no row is a bare word', () => {
    for (const category of SETTINGS_CATEGORIES) {
      expect(category.label.length).toBeGreaterThan(0);
      expect(category.blurb.length).toBeGreaterThan(0);
    }
  });

  it('groups the rows, so the menu is not one undivided list', () => {
    expect(new Set(SETTINGS_CATEGORIES.map((c) => c.group)).size).toBeGreaterThan(1);
  });

  it('PAGE_KEYS is exactly the addressable set, in menu order', () => {
    expect(PAGE_KEYS).toEqual(['rules', 'display', 'automation', 'sources', 'system']);
  });
});

describe('settingsFromSearch', () => {
  it('reads a page category out of the URL', () => {
    expect(settingsFromSearch('?settings=automation')).toBe('automation');
    expect(settingsFromSearch('?minimum_score=70&settings=display')).toBe('display');
  });

  it('lands on the dashboard when nothing is asked for', () => {
    expect(settingsFromSearch('')).toBeNull();
    expect(settingsFromSearch('?minimum_score=70')).toBeNull();
  });

  it('a stale or hand-edited value lands on the dashboard, never on a blank page', () => {
    expect(settingsFromSearch('?settings=nonsense')).toBeNull();
    expect(settingsFromSearch('?settings=')).toBeNull();
  });

  it('refuses to address the filters panel, which the URL already describes', () => {
    // The filters themselves are query parameters. A second representation of
    // the same state is a second thing that can disagree with the first.
    expect(settingsFromSearch('?settings=filters')).toBeNull();
  });
});

describe('withSettings', () => {
  it('adds the category without disturbing the filter parameters', () => {
    const out = withSettings('minimum_score=70&active_only=true', 'automation');
    const params = new URLSearchParams(out);
    expect(params.get('settings')).toBe('automation');
    expect(params.get('minimum_score')).toBe('70');
    expect(params.get('active_only')).toBe('true');
  });

  it('removes it again on the way back to the dashboard', () => {
    const out = withSettings('minimum_score=70&settings=display', null);
    expect(new URLSearchParams(out).has('settings')).toBe(false);
    expect(new URLSearchParams(out).get('minimum_score')).toBe('70');
  });

  it('replaces rather than appends, so the URL cannot carry two categories', () => {
    const out = withSettings('settings=display', 'system');
    expect(new URLSearchParams(out).getAll('settings')).toEqual(['system']);
  });

  it('never writes a panel category into the URL', () => {
    expect(new URLSearchParams(withSettings('', 'filters')).has('settings')).toBe(false);
  });

  it('round-trips every addressable category', () => {
    for (const key of PAGE_KEYS) {
      expect(settingsFromSearch('?' + withSettings('', key))).toBe(key);
    }
  });
});
