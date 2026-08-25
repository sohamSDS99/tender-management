import type { IconName } from '../components/Icon';

/**
 * Settings as categories, not one ten-section scroll.
 *
 * The panel used to hold presets, score, fit, deployment, capability, sources,
 * country, dates, display *and* automation in a single 340px column. Everything
 * was one click away, which sounds good until you are looking for the theme
 * toggle and have to scroll past seven filter groups to find it.
 *
 * So the rail button now opens a menu of categories, and a category opens its
 * own surface. Which surface depends on what the category is *for*, and this is
 * the one part worth being careful about:
 *
 * * **Filters** opens the side panel, because filtering is the most frequent
 *   thing anyone does here and the panel deliberately carries no scrim - the
 *   results stay visible and live to its right, so you can watch a filter take
 *   effect while you are still setting it. A full-width page would hide the very
 *   list you are narrowing.
 * * **Everything else** opens a full-width page. Choosing a theme or a sweep
 *   time is a thing you do occasionally and deliberately, and none of it needs
 *   the tender list in view.
 */
export type SettingsKey = 'filters' | 'display' | 'automation' | 'sources' | 'system';

export interface SettingsCategory {
  key: SettingsKey;
  label: string;
  /** One line under the label in the menu. Says what is inside, not why. */
  blurb: string;
  icon: IconName;
  /** 'panel' keeps the results live beside it; 'page' takes the width. */
  surface: 'panel' | 'page';
  /** Menu rows are divided into groups, as in any settings menu. */
  group: number;
}

export const SETTINGS_CATEGORIES: SettingsCategory[] = [
  {
    key: 'filters',
    label: 'Filters',
    blurb: 'Score, fit, sources, dates',
    icon: 'sliders',
    surface: 'panel',
    group: 1,
  },
  {
    key: 'display',
    label: 'Display',
    blurb: 'Theme, density, page size',
    icon: 'display',
    surface: 'page',
    group: 2,
  },
  {
    key: 'automation',
    label: 'Automation',
    blurb: 'Schedule, pause and resume',
    icon: 'clock',
    surface: 'page',
    group: 2,
  },
  {
    key: 'sources',
    label: 'Sources',
    blurb: 'Connector health and coverage',
    icon: 'grid',
    surface: 'page',
    group: 2,
  },
  {
    key: 'system',
    label: 'System',
    blurb: 'Slack delivery, links, versions',
    icon: 'info',
    surface: 'page',
    group: 3,
  },
];

const BY_KEY = new Map(SETTINGS_CATEGORIES.map((c) => [c.key, c]));

export function categoryFor(key: string | null | undefined): SettingsCategory | null {
  return key ? (BY_KEY.get(key as SettingsKey) ?? null) : null;
}

/** The categories that take the width, in menu order. */
export const PAGE_KEYS: SettingsKey[] = SETTINGS_CATEGORIES.filter((c) => c.surface === 'page').map(
  (c) => c.key,
);

/**
 * Which settings page a URL is asking for, or null for the dashboard.
 *
 * Only page categories are addressable. Filters are not: the panel's open state
 * is a stored preference, and the filters themselves already round-trip through
 * the URL as query parameters - putting `settings=filters` there too would give
 * one thing two representations that could disagree.
 *
 * An unrecognised value is null rather than an error. A stale or hand-edited
 * link must land on the dashboard, not on a blank page.
 */
export function settingsFromSearch(search: string): SettingsKey | null {
  const raw = new URLSearchParams(search).get('settings');
  const found = categoryFor(raw);
  return found && found.surface === 'page' ? found.key : null;
}

/**
 * Add or remove `settings` on an existing query string, leaving the rest alone.
 *
 * Takes the string the filter codec already produced rather than rebuilding it,
 * so there is still exactly one place that knows how filters are serialised.
 */
export function withSettings(search: string, key: SettingsKey | null): string {
  const params = new URLSearchParams(search);
  if (key && categoryFor(key)?.surface === 'page') {
    params.set('settings', key);
  } else {
    params.delete('settings');
  }
  return params.toString();
}
