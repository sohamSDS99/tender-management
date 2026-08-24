import type { TenderFilters } from '../types';
import { VIEWS, type ViewContext, type ViewKey, activeView } from '../state/views';

/**
 * The primary navigation: four questions, not five metrics.
 *
 * Deliberately without counts. The numbers came from /api/stats, whose predicates
 * are not the ones the views apply — `good_fit_or_better` has no actionability
 * clause, and none of them know about a search term or a source filter. So a tab
 * could read "6" directly above a result count of "1". The count line under the
 * toolbar is derived from the same query as the list, so it is the one that can
 * be trusted, and having only one number on screen removes the contradiction.
 *
 * "New" is disabled until a sweep has run, and says why in visible text rather
 * than in a title attribute nobody hovers.
 */
export function Views({
  filters,
  context,
  onSelect,
}: {
  filters: TenderFilters;
  context: ViewContext;
  onSelect: (key: ViewKey) => void;
}) {
  const current = activeView(filters, context);

  return (
    <nav className="views" aria-label="Views">
      {VIEWS.map((view) => {
        const disabled = view.key === 'new' && !context.lastRunAt;
        return (
          <button
            key={view.key}
            type="button"
            className={`view${current === view.key ? ' is-on' : ''}`}
            aria-current={current === view.key ? 'page' : undefined}
            disabled={disabled}
            onClick={() => onSelect(view.key)}
          >
            {view.label}
            {disabled && view.unavailable ? (
              <span className="view__note">{view.unavailable}</span>
            ) : null}
          </button>
        );
      })}
    </nav>
  );
}
