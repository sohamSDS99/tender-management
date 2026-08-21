import type { Stats, TenderFilters } from '../types';
import { DEFAULT_FILTERS } from '../state/urlFilters';

/**
 * Stat tiles are filters (delta 2).
 *
 * Each carries a status dot and a one-line explanation of what the number means,
 * and clicking one applies the filter it describes. "Needs review" earns its
 * place where a dead connector count used to sit; "Connector problems" opens the
 * source health strip rather than filtering, because that is the actionable move.
 */
export type TileKey = 'stored' | 'relevant' | 'closing' | 'review' | 'sources';

export interface StatTilesProps {
  stats: Stats | null;
  filters: TenderFilters;
  onApply: (patch: Partial<TenderFilters>) => void;
  onShowSources: () => void;
  failedSources: number;
  failedSummary: string;
}

function isActive(filters: TenderFilters, key: TileKey): boolean {
  const relevantBar = 70;
  switch (key) {
    case 'stored':
      return filters.minimum_score === 0 && !filters.active_only;
    case 'relevant':
      return filters.minimum_score >= relevantBar && filters.fit_statuses.length === 0;
    case 'closing':
      return filters.deadline_to !== '' && filters.active_only;
    case 'review':
      return filters.fit_statuses.length === 1 && filters.fit_statuses[0] === 'manual_review';
    default:
      return false;
  }
}

function inDays(days: number): string {
  const date = new Date(Date.now() + days * 86_400_000);
  return date.toISOString().slice(0, 10);
}

export function StatTiles({
  stats,
  filters,
  onApply,
  onShowSources,
  failedSources,
  failedSummary,
}: StatTilesProps) {
  const n = (value: number | undefined) => (value ?? 0).toLocaleString('en-GB');
  const bar = stats?.score_bands?.good_fit ?? 70;

  const tiles: {
    key: TileKey;
    dot: string;
    label: string;
    value: string;
    sub: string;
    hint: string;
    onClick: () => void;
  }[] = [
    {
      key: 'stored',
      dot: 'dot--brand',
      label: 'Tenders stored',
      value: n(stats?.total_tenders),
      sub: 'Everything ever ingested, all scores',
      hint: 'Show all →',
      onClick: () =>
        onApply({ ...DEFAULT_FILTERS, minimum_score: 0, active_only: false, sort: filters.sort }),
    },
    {
      key: 'relevant',
      dot: 'dot--good',
      label: 'Highly relevant',
      value: n(stats?.good_fit_or_better),
      sub: `Score ${bar} or higher — the Slack digest bar`,
      hint: 'Filter →',
      onClick: () =>
        onApply({ minimum_score: bar, maximum_score: 100, fit_statuses: [], active_only: true }),
    },
    {
      key: 'closing',
      dot: 'dot--warning',
      label: 'Closing ≤ 14 days',
      value: n(stats?.closing_soon),
      sub: 'Open, relevant, and a deadline within a fortnight',
      hint: 'Filter →',
      onClick: () =>
        onApply({
          deadline_from: inDays(0),
          deadline_to: inDays(14),
          active_only: true,
          sort: 'deadline_asc',
        }),
    },
    {
      key: 'review',
      dot: 'dot--serious',
      label: 'Needs review',
      value: n(stats?.by_fit_status.find((b) => b.key === 'manual_review')?.count),
      sub: 'Hybrid hosting or an ambiguous SDS match',
      hint: 'Filter →',
      onClick: () =>
        onApply({ fit_statuses: ['manual_review'], minimum_score: 0, active_only: true }),
    },
    {
      key: 'sources',
      dot: failedSources > 0 ? 'dot--critical' : 'dot--good',
      label: 'Connector problems',
      value: n(failedSources),
      sub: failedSummary,
      hint: 'Show sources →',
      onClick: onShowSources,
    },
  ];

  return (
    <section className="stats" aria-label="Summary — each tile applies a filter">
      {tiles.map((tile) => (
        <button
          key={tile.key}
          type="button"
          className={`stat${tile.key === 'sources' ? ' stat--wide' : ''}${
            isActive(filters, tile.key) ? ' is-active' : ''
          }`}
          onClick={tile.onClick}
          aria-pressed={isActive(filters, tile.key)}
        >
          <span className="stat__top">
            <span className={`dot ${tile.dot}`} />
            <span className="stat__label">{tile.label}</span>
          </span>
          <span className="stat__value num">{stats ? tile.value : '—'}</span>
          <span className="stat__sub">{tile.sub}</span>
          <span className="stat__hint">{tile.hint}</span>
        </button>
      ))}
    </section>
  );
}
