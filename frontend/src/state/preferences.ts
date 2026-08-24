import { useCallback, useEffect, useState } from 'react';
import type { Preferences, Theme } from '../types';

/**
 * Theme and card density.
 *
 * Both are attributes on <html> because that is what the ported stylesheet keys
 * off (`html[data-theme="dark"]`, `html[data-density="compact"]`). Stored in
 * localStorage so a reload keeps the reader's choice; 'system' follows the OS
 * and keeps following it if the OS setting changes mid-session.
 */
const STORAGE_KEY = 'tender-monitor:preferences';

export const DEFAULT_PREFERENCES: Preferences = { theme: 'system' };

function isTheme(value: unknown): value is Theme {
  return value === 'light' || value === 'dark' || value === 'system';
}

export function readPreferences(): Preferences {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFERENCES;
    const parsed = JSON.parse(raw) as Partial<Preferences>;
    return { theme: isTheme(parsed.theme) ? parsed.theme : DEFAULT_PREFERENCES.theme };
  } catch {
    // A private-mode browser or a corrupt value must not stop the app rendering.
    return DEFAULT_PREFERENCES;
  }
}

function prefersDark(): boolean {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
}

export function resolveTheme(theme: Theme): 'light' | 'dark' {
  return theme === 'system' ? (prefersDark() ? 'dark' : 'light') : theme;
}

export function usePreferences() {
  const [preferences, setPreferences] = useState<Preferences>(readPreferences);
  // Held in state, not just written to the DOM: the toggle's icon and label are
  // derived from it, so an OS theme change has to cause a re-render or the
  // button ends up promising the opposite of what it does.
  const [systemDark, setSystemDark] = useState(prefersDark);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme =
      preferences.theme === 'system' ? (systemDark ? 'dark' : 'light') : preferences.theme;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
    } catch {
      /* storage unavailable: the attributes above still applied */
    }
  }, [preferences, systemDark]);

  // Keep following the OS while the reader has chosen 'system'.
  useEffect(() => {
    if (preferences.theme !== 'system') return;
    const query = window.matchMedia?.('(prefers-color-scheme: dark)');
    if (!query) return;
    const apply = () => setSystemDark(prefersDark());
    apply();
    query.addEventListener('change', apply);
    return () => query.removeEventListener('change', apply);
  }, [preferences.theme]);

  const update = useCallback((patch: Partial<Preferences>) => {
    setPreferences((prev) => ({ ...prev, ...patch }));
  }, []);

  const toggleTheme = useCallback(() => {
    setPreferences((prev) => ({
      ...prev,
      theme:
        (prev.theme === 'system' ? (prefersDark() ? 'dark' : 'light') : prev.theme) === 'dark'
          ? 'light'
          : 'dark',
    }));
  }, []);

  // The theme actually in force, which 'system' alone cannot tell the caller.
  const resolved: 'light' | 'dark' =
    preferences.theme === 'system' ? (systemDark ? 'dark' : 'light') : preferences.theme;

  return { preferences, resolved, update, toggleTheme };
}
