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
