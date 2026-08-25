import { useCallback, useEffect, useState } from 'react';
import type { Density, Preferences } from '../types';

/**
 * Card density and whether the settings panel is open.
 *
 * Density is an attribute on <html> because that is what the stylesheet keys
 * off (`html[data-density="compact"]`). Stored in localStorage so a reload
 * keeps the reader's choice.
 */
/**
 * Versioned, and v2 deliberately ignores v1.
 *
 * v1's default was 'system', so almost every stored value is that default rather
 * than a choice anyone made — and reading it back would have overridden the new
 * dark default for everyone who had ever loaded the old page. Starting clean
 * costs one re-pick for the few who genuinely chose light or system.
 */
const STORAGE_KEY = 'tender-monitor:preferences:v2';

export const DEFAULT_PREFERENCES: Preferences = {
  density: 'comfortable',
  settingsOpen: true,
};

function isDensity(value: unknown): value is Density {
  return value === 'comfortable' || value === 'compact';
}

export function readPreferences(): Preferences {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFERENCES;
    const parsed = JSON.parse(raw) as Partial<Preferences>;
    return {
      density: isDensity(parsed.density) ? parsed.density : DEFAULT_PREFERENCES.density,
      settingsOpen:
        typeof parsed.settingsOpen === 'boolean'
          ? parsed.settingsOpen
          : DEFAULT_PREFERENCES.settingsOpen,
    };
  } catch {
    // A private-mode browser or a corrupt value must not stop the app rendering.
    return DEFAULT_PREFERENCES;
  }
}

export function usePreferences() {
  const [preferences, setPreferences] = useState<Preferences>(readPreferences);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.density = preferences.density;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
    } catch {
      /* storage unavailable: the attribute above still applied */
    }
  }, [preferences]);

  const update = useCallback((patch: Partial<Preferences>) => {
    setPreferences((prev) => ({ ...prev, ...patch }));
  }, []);

  return { preferences, update };
}
