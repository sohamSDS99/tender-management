import { expect, it } from 'vitest';
import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import { resolveTheme, usePreferences } from './state/preferences';

// Temporary probe (review only) — reproduces Masthead's label in isolation.
function makeMatchMedia(initialDark: boolean) {
  let dark = initialDark;
  const listeners = new Set<(e: { matches: boolean }) => void>();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (window as any).matchMedia = (q: string) => ({
    matches: q.includes('dark') ? dark : false,
    media: q,
    addEventListener: (_t: string, fn: (e: { matches: boolean }) => void) => listeners.add(fn),
    removeEventListener: (_t: string, fn: (e: { matches: boolean }) => void) => listeners.delete(fn),
    dispatchEvent: () => true,
    onchange: null,
  });
  return {
    set(next: boolean) {
      dark = next;
      listeners.forEach((fn) => fn({ matches: dark }));
    },
  };
}

function Probe() {
  const { preferences, toggleTheme } = usePreferences();
  const theme = resolveTheme(preferences.theme);
  return createElement('button', {
    onClick: toggleTheme,
    'aria-label': `Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`,
  });
}

it('probe: OS flip mid-session', async () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  window.localStorage.setItem('tender-monitor:preferences', JSON.stringify({ theme: 'system' }));
  const mm = makeMatchMedia(true); // OS dark
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(createElement(Probe));
  });
  const btn = () => container.querySelector('button')!;
  const snap = (tag: string) =>
    // eslint-disable-next-line no-console
    console.log(
      tag,
      JSON.stringify({
        dataTheme: document.documentElement.dataset.theme,
        label: btn().getAttribute('aria-label'),
        stored: window.localStorage.getItem('tender-monitor:preferences'),
      }),
    );
  snap('1 mounted (OS dark):');

  await act(async () => {
    mm.set(false); // OS flips to light, no other interaction
  });
  snap('2 after OS -> light:');

  await act(async () => {
    btn().dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  snap('3 after one click:');

  await act(async () => {
    btn().dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  snap('4 after second click:');
  expect(true).toBe(true);
});
