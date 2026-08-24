import { describe, expect, it } from 'vitest';
import { resolveWithSystem, toggledTheme } from './preferences';

describe('resolveWithSystem', () => {
  it('follows the OS only when the choice is "system"', () => {
    expect(resolveWithSystem('system', true)).toBe('dark');
    expect(resolveWithSystem('system', false)).toBe('light');
  });

  it('an explicit choice beats the OS', () => {
    expect(resolveWithSystem('light', true)).toBe('light');
    expect(resolveWithSystem('dark', false)).toBe('dark');
  });
});

describe('toggledTheme', () => {
  it('always moves away from what is currently on screen', () => {
    expect(toggledTheme('light', false)).toBe('dark');
    expect(toggledTheme('dark', true)).toBe('light');
  });

  it('honours the OS when following it, so one press matches the label', () => {
    // Regression: after the OS switched to dark mid-session the button still
    // read "Switch to dark" and one press selected dark again — a no-op that
    // looked broken.
    expect(toggledTheme('system', true)).toBe('light');
    expect(toggledTheme('system', false)).toBe('dark');
  });

  it('never returns "system", so a press is always an explicit choice', () => {
    for (const theme of ['light', 'dark', 'system'] as const) {
      for (const dark of [true, false]) {
        expect(['light', 'dark']).toContain(toggledTheme(theme, dark));
      }
    }
  });
});
