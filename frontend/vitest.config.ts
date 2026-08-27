import { defineConfig } from 'vitest/config';

// jsdom is needed because the modules under test touch `window` (urlFilters
// reads location/URLSearchParams, labels.safeHref resolves against
// window.location.origin) and because App.test.tsx mounts real components.
//
// `.tsx` is in the glob deliberately. It used to be `.ts` only, which meant a
// component test could be written and would simply never run - the sign-in gate
// shipped with no client-side coverage for exactly that reason.
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['src/test-setup.ts'],
  },
});
