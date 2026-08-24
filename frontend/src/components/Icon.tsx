/**
 * One icon set, one stroke weight (1.6), one 24-grid.
 *
 * Drawn rather than pulled from a library: the app ships only react and
 * react-dom, and these fourteen paths are smaller than any icon package. Every
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
  | 'link';

const PATHS: Record<IconName, string[]> = {
  check: ['M4.5 12.5l4.5 4.5L19.5 6.5'],
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
      strokeWidth={1.6}
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
