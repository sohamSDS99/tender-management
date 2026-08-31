/**
 * One icon set, one stroke weight (1.6), one 24-grid.
 *
 * Drawn rather than pulled from a library: the app ships only react and
 * react-dom, and these paths are smaller than any icon package. Every
 * icon is decorative — the meaning is always in adjacent text — so they are
 * aria-hidden and never the sole carrier of a state.
 */

export type IconName =
  | 'check'
  | 'warn'
  | 'block'
  | 'clock'
  | 'external'
  | 'search'
  | 'close'
  | 'sliders'
  | 'chevronRight'
  | 'prev'
  | 'next'
  | 'sun'
  | 'moon'
  | 'copy'
  | 'link'
  | 'download'
  | 'refresh'
  | 'info'
  | 'chevronDown'
  | 'settings'
  | 'display'
  | 'grid'
  | 'user'
  | 'users'
  | 'signout'
  | 'translate';

const PATHS: Record<IconName, string[]> = {
  check: ['M4.5 12.5l4.5 4.5L19.5 6.5'],
  // Two scripts side by side rather than a globe: a globe reads as
  // "language of the site", and this button changes one block of text.
  translate: [
    'M3.5 5.5h8',
    'M7 3.5v2',
    'M9.5 5.5c0 3.9-2.4 7-6 8.5',
    'M4.5 10c0 1.9 2.1 3.5 4.7 3.5',
    'M12.5 20.5l4-9 4 9',
    'M13.9 17.5h5.2',
  ],
  warn: ['M12 9.5v4.5', 'M12 17.5h.01', 'M12 3.5 2.8 19.5h18.4L12 3.5z'],
  block: ['M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z', 'M9 15l6-6'],
  clock: ['M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z', 'M12 7.5V12l3 2'],
  external: [
    'M14 4.5h5.5V10',
    'M19.5 4.5 11 13',
    'M18 14.5v4a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6h4',
  ],
  search: ['M10.5 3.5a7 7 0 1 0 0 14 7 7 0 0 0 0-14z', 'M20.5 20.5l-5-5'],
  close: ['M6 6l12 12', 'M18 6 6 18'],
  sliders: ['M4 7h10', 'M18 7h2', 'M4 17h4', 'M12 17h8', 'M16 4.5v5', 'M8 14.5v5'],
  chevronRight: ['M9.5 5.5l7 6.5-7 6.5'],
  prev: ['M14.5 5.5 8 12l6.5 6.5'],
  next: ['M9.5 5.5 16 12l-6.5 6.5'],
  sun: [
    'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
    'M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4',
  ],
  moon: ['M20.5 13.2A8.5 8.5 0 1 1 10.8 3.5a6.6 6.6 0 0 0 9.7 9.7z'],
  copy: [
    'M9.5 9.5h9a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z',
    'M5.5 15.5h-1a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v1',
  ],
  link: [
    'M10.5 13.5a4 4 0 0 0 5.7 0l2.3-2.3a4 4 0 0 0-5.7-5.7L11.5 6.7',
    'M13.5 10.5a4 4 0 0 0-5.7 0l-2.3 2.3a4 4 0 0 0 5.7 5.7l1.3-1.3',
  ],
  download: ['M12 3.5v11', 'm7.5 10 4.5 4.5 4.5-4.5', 'M4.5 20.5h15'],
  refresh: ['M20.5 12a8.5 8.5 0 1 1-2.8-6.3', 'M20.5 3.5v6h-6'],
  info: ['M12 16v-5', 'M12 8h.01', 'M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17z'],
  chevronDown: ['m6 9.5 6 6 6-6'],
  display: [
    'M4.5 4.5h15a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z',
    'M9 19.5h6',
    'M12 15.5v4',
  ],
  grid: ['M4.5 4.5h6v6h-6z', 'M13.5 4.5h6v6h-6z', 'M4.5 13.5h6v6h-6z', 'M13.5 13.5h6v6h-6z'],
  settings: ['M4 7h10', 'M18 7h2', 'M4 17h4', 'M12 17h8', 'M16 4.5v5', 'M8 14.5v5'],
  user: ['M12 3.8a3.9 3.9 0 1 0 0 7.8 3.9 3.9 0 0 0 0-7.8z', 'M4.5 20.2a7.5 7.5 0 0 1 15 0'],
  users: [
    'M9 4.2a3.6 3.6 0 1 0 0 7.2 3.6 3.6 0 0 0 0-7.2z',
    'M2.8 19.8a6.2 6.2 0 0 1 12.4 0',
    'M16.2 4.6a3.6 3.6 0 0 1 0 6.9',
    'M17.6 13.8a6.2 6.2 0 0 1 3.6 5.6',
  ],
  signout: [
    'M14.5 4.5h4a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-4',
    'M4.5 12h10',
    'm11 8.5 3.5 3.5-3.5 3.5',
  ],
};

export function Icon({
  name,
  size = 14,
  className,
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      // Fixed in user units, a single value renders a different optical weight at
      // every call size (0.73px at 11, 1.07px at 16). Scaling keeps it at 1.5px.
      strokeWidth={(1.5 * 24) / size}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
