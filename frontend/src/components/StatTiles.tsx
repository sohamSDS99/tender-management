import type { SourceStatus, Stats, TenderFilters } from '../types';
import { TILES, type TileKey, type ViewContext } from '../state/views';
import { Icon } from './Icon';

const TONE_CLASS = {
  brand: 'dot--brand',
  good: 'dot--good',
  warning: 'dot--warning',
  serious: 'dot--serious',
  critical: 'dot--critical',
} as const;

/**
 * The five summary tiles.
 *
 * Every tile is a filter, not an ornament: clicking it narrows the list to the
 * exact population it counted. That is the only way a number on a dashboard can
 * be checked, and it is why the counts come from `/api/stats` rather than from the
 * filtered page — a facet count taken from a narrowed list promises results that
 * are not there.
 */
export function StatTiles({
  stats,
  sources,
  activeTile,
  onApply,
  onShowSources,
}: {
  stats: Stats | null;
  sources: SourceStatus[];
  activeTile: TileKey | null;
  onApply: (patch: Partial<TenderFilters>) => void;
  onShowSources: () => void;
}) {
  const context: ViewContext = {
    lastRunAt: null,
    goodFitBand: stats?.score_bands?.good_fit ?? 70,
    possibleFitBand: stats?.score_bands?.possible_fit ?? 50,
  };

  const broken = sources.filter((s) => s.unavailable_reason || s.last_status === 'failed');
  const closingSoon = stats?.closing_soon ?? 0;

  const value = (key: TileKey): number => {
    if (!stats) return 0;
    switch (key) {
      case 'open':
        return stats.actionable;
      case 'topscoring':
        return stats.good_fit_or_better;
      case 'closing':
        return closingSoon;
      case 'review':
        return stats.possible_or_review;
      default:
        return 0;
    }
  };

  const sub = (key: TileKey): string => {
    switch (key) {
      case 'open':
        return 'Still accepting bids';
      case 'topscoring':
        return `Score ${context.goodFitBand} or higher`;
      case 'closing':
        return 'Deadline within two weeks';
      case 'review':
        return 'Ambiguous fit or hosting';
      default:
        return '';
    }
  };

  return (
    <section className="stats" aria-label="Summary — each tile applies a filter">
      {TILES.map((tile) => (
        <button
          key={tile.key}
          type="button"
          className={`stat${activeTile === tile.key ? ' is-active' : ''}`}
          aria-pressed={activeTile === tile.key}
          onClick={() => onApply(tile.patch(context))}
        >
          <span className="stat__top">
            <span className={`dot ${TONE_CLASS[tile.tone]}`} aria-hidden="true" />
            <span className="stat__label">{tile.label}</span>
          </span>
          <span className="stat__value num">
            {stats ? value(tile.key).toLocaleString('en-GB') : '—'}
          </span>
          <span className="stat__sub">{sub(tile.key)}</span>
          <span className="stat__hint" aria-hidden="true">
            Filter →
          </span>
        </button>
      ))}

      {/* Not a filter — a jump to the panel that explains it, because a broken
          connector is not a property of any tender you could filter for. */}
      <button type="button" className="stat" onClick={onShowSources}>
        <span className="stat__top">
          <span
            className={`dot ${broken.length ? 'dot--critical' : 'dot--good'}`}
            aria-hidden="true"
          />
          <span className="stat__label">Connector problems</span>
        </span>
        <span className="stat__value num">{broken.length}</span>
        <span className="stat__sub">
          {broken.length === 0
            ? 'Every source reporting'
            : broken
                .slice(0, 2)
                .map((s) => s.display_name)
                .join(', ')}
        </span>
        <span className="stat__hint" aria-hidden="true">
          Show sources →
        </span>
      </button>
    </section>
  );
}

/** The bucket note strip under the tabs. */
export function BucketNote({ text }: { text: string }) {
  return (
    <p className="bucketnote">
      <Icon name="info" size={14} />
      <span>{text}</span>
    </p>
  );
}
