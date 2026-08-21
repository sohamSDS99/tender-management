/**
 * The mockup's inline SVGs, collected into one place.
 *
 * Kept as a single component rather than a dependency: the app ships only react
 * and react-dom, and an icon library would be far larger than these ten paths.
 * Every icon is decorative - meaning always comes from adjacent text - so they
 * are aria-hidden and never the only carrier of a status.
 */

export type IconName =
  | 'check'
  | 'warning'
  | 'cross'
  | 'clock'
  | 'external'
  | 'search'
  | 'sliders'
  | 'chevron'
  | 'close'
  | 'sun'
  | 'moon'
  | 'prev'
  | 'next'
  | 'copy'
  | 'link'
  | 'refresh'
  | 'calendar';

const PATHS: Record<IconName, { d: string[]; width?: number; fill?: string[] }> = {
  check: { d: ['m4 13 5 5L20 6'], width: 3 },
  warning: {
    d: [
      'M12 9v4M12 17h.01',
      'M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z',
    ],
    width: 2.4,
  },
  cross: { d: ['M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0', 'm9 9 6 6M15 9l-6 6'], width: 2.4 },
  clock: { d: ['M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0', 'M12 7v5l3 2'], width: 2.4 },
  external: { d: ['M7 17 17 7M9 7h8v8'], width: 2.4 },
  search: { d: ['M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0', 'm20 20-3.5-3.5'], width: 2.2 },
  sliders: {
    d: ['M4 6h16M4 12h16M4 18h16'],
    width: 2.2,
    fill: [
      'M9 6m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0',
      'M15 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0',
      'M8 18m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0',
    ],
  },
  chevron: { d: ['m6 9 6 6 6-6'], width: 2.2 },
  close: { d: ['M6 6l12 12M18 6 6 18'], width: 2.2 },
  sun: {
    d: [
      'M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0',
      'M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
    ],
    width: 2,
  },
  moon: { d: ['M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z'], width: 2 },
  prev: { d: ['m15 6-6 6 6 6'], width: 2.4 },
  next: { d: ['m9 6 6 6-6 6'], width: 2.4 },
  copy: { d: ['M9 9h10v10H9z', 'M5 15V5h10'], width: 2 },
  link: {
    d: [
      'M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1',
      'M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1',
    ],
    width: 2,
  },
  refresh: { d: ['M21 12a9 9 0 1 1-3-6.7', 'M21 3v6h-6'], width: 2.2 },
  calendar: { d: ['M4 6h16v15H4z', 'M8 3v4M16 3v4M4 11h16'], width: 2 },
};

export interface IconProps {
  name: IconName;
  size?: number;
  className?: string;
}

export function Icon({ name, size = 14, className }: IconProps) {
  const spec = PATHS[name];
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={spec.width ?? 2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {spec.d.map((d) => (
        <path key={d} d={d} />
      ))}
      {spec.fill?.map((d) => (
        <path key={d} d={d} fill="currentColor" stroke="none" />
      ))}
    </svg>
  );
}
