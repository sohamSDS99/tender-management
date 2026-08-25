import type { Density, Theme } from '../../types';
import { PAGE_SIZES } from '../../state/urlFilters';
import { SettingsPage, SettingsRow, SettingsSection } from './SettingsPage';

const THEMES: { value: Theme; label: string; hint: string }[] = [
  { value: 'dark', label: 'Dark', hint: 'the default' },
  { value: 'light', label: 'Light', hint: '' },
  { value: 'system', label: 'System', hint: 'follows the OS' },
];

/**
 * How the dashboard looks and how much of it fits on a screen.
 *
 * Nothing here changes what is in the list, only how it reads — which is exactly
 * why it belongs on a page rather than beside the results. These are decisions
 * made once and left alone, and they used to sit at the bottom of a scroll under
 * seven groups of filters.
 */
export function DisplaySettings({
  theme,
  density,
  pageSize,
  resolvedTheme,
  onTheme,
  onDensity,
  onPageSize,
  onBack,
}: {
  theme: Theme;
  density: Density;
  pageSize: number;
  /** What 'system' currently resolves to, so the page can say so. */
  resolvedTheme: 'light' | 'dark';
  onTheme: (theme: Theme) => void;
  onDensity: (density: Density) => void;
  onPageSize: (size: number) => void;
  onBack: () => void;
}) {
  return (
    <SettingsPage
      title="Display"
      blurb="Appearance and layout. These are stored in this browser and affect nobody else."
      onBack={onBack}
    >
      <SettingsSection
        title="Theme"
        note="Dark is the design's own theme; light is a full override."
      >
        <SettingsRow
          label="Colour theme"
          hint={
            theme === 'system'
              ? `Following the operating system, which is currently ${resolvedTheme}.`
              : undefined
          }
        >
          <div className="seg" role="group" aria-label="Colour theme">
            {THEMES.map((option) => (
              <button
                key={option.value}
                type="button"
                className={theme === option.value ? 'is-on' : undefined}
                aria-pressed={theme === option.value}
                onClick={() => onTheme(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </SettingsRow>
      </SettingsSection>

      <SettingsSection title="Layout" note="How much of the list fits on one screen.">
        <SettingsRow
          label="Card density"
          hint="Compact shortens each result card and clamps its title to one line."
        >
          <div className="seg" role="group" aria-label="Card density">
            {(['comfortable', 'compact'] as Density[]).map((value) => (
              <button
                key={value}
                type="button"
                className={density === value ? 'is-on' : undefined}
                aria-pressed={density === value}
                onClick={() => onDensity(value)}
              >
                {value === 'comfortable' ? 'Comfortable' : 'Compact'}
              </button>
            ))}
          </div>
        </SettingsRow>

        <SettingsRow
          label="Results per page"
          hint="Larger pages mean fewer clicks and a slower first paint."
        >
          <div className="seg" role="group" aria-label="Results per page">
            {PAGE_SIZES.map((size) => (
              <button
                key={size}
                type="button"
                className={pageSize === size ? 'is-on' : undefined}
                aria-pressed={pageSize === size}
                onClick={() => onPageSize(size)}
              >
                {size}
              </button>
            ))}
          </div>
        </SettingsRow>
      </SettingsSection>
    </SettingsPage>
  );
}
