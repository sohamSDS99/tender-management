import type { Stats } from '../types';
import { formatDateTime } from '../labels';

interface Props {
  stats: Stats | null;
  loading: boolean;
}

export function StatsBar({ stats, loading }: Props) {
  const cards = [
    { label: 'Tenders stored', value: stats?.total_tenders, tone: 'neutral' },
    { label: 'Highly relevant (70+)', value: stats?.good_fit_or_better, tone: 'green' },
    { label: 'Closing within 14 days', value: stats?.closing_soon, tone: 'amber' },
    { label: 'Failed connectors', value: stats?.failed_sources, tone: 'red' },
  ] as const;

  return (
    <section className="stats" aria-label="Summary">
      {cards.map((card) => (
        <div key={card.label} className={`stat stat--${card.tone}`}>
          <span className="stat__value">{loading && !stats ? '·' : (card.value ?? 0)}</span>
          <span className="stat__label">{card.label}</span>
        </div>
      ))}
      <div className="stat stat--wide">
        <span className="stat__value stat__value--small">
          {formatDateTime(stats?.last_successful_fetch)}
        </span>
        <span className="stat__label">Last successful fetch</span>
      </div>
    </section>
  );
}
