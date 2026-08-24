import type { Stats, TenderFilters } from '../types';
import { VIEWS, type ViewContext, type ViewKey, activeView } from '../state/views';

/**
 * The primary navigation: four questions, not five metrics.
 *
 * Counts come from /api/stats where the same number is already computed, so a
 * tab can say how much is behind it without a second request. "New" is disabled
 * until a sweep has actually run, because otherwise it silently shows everything.
 */
export function Views({
  filters,
  stats,
  context,
  onSelect,
}: {
  filters: TenderFilters;
  stats: Stats | null;
  context: ViewContext;
  onSelect: (key: ViewKey) => void;
}) {
  const current = activeView(filters, context);

  const countFor = (key: ViewKey): number | null => {
    if (!stats) return null;
    if (key === 'attention') return stats.good_fit_or_better;
    if (key === 'closing') return stats.closing_soon;
    if (key === 'all') return stats.total_tenders;
    return null; // New has no precomputed count; the result count carries it
  };

  return (
    <nav className="views" aria-label="Views">
      {VIEWS.map((view) => {
        const disabled = view.key === 'new' && !context.lastRunAt;
        const count = countFor(view.key);
        return (
          <button
            key={view.key}
            type="button"
            className={`view${current === view.key ? ' is-on' : ''}`}
            aria-current={current === view.key ? 'page' : undefined}
            disabled={disabled}
            title={disabled ? view.unavailable : undefined}
            onClick={() => onSelect(view.key)}
          >
            {view.label}
            {count !== null ? (
              <span className="view__n">{count.toLocaleString('en-GB')}</span>
            ) : null}
          </button>
        );
      })}
    </nav>
  );
}
