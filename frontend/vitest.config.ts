import { defineConfig } from 'vitest/config';

// jsdom is needed only because the modules under test touch `window`:
// urlFilters reads location/URLSearchParams and labels.safeHref resolves
// against window.location.origin.
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
  },
});
