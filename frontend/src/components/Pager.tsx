import type { TenderPage } from '../types';
import { Icon } from './Icon';

/** Numbered pagination with an ellipsis, as in the mockup. */
export function Pager({ page, onGo }: { page: TenderPage; onGo: (next: number) => void }) {
  if (page.pages <= 1) return null;

  const current = page.page;
  const total = page.pages;
  const first = (current - 1) * page.page_size + 1;
  const last = Math.min(current * page.page_size, page.total);

  const numbers: (number | 'gap')[] = [];
  for (let n = 1; n <= total; n += 1) {
    if (n === 1 || n === total || Math.abs(n - current) <= 1) numbers.push(n);
    else if (numbers[numbers.length - 1] !== 'gap') numbers.push('gap');
  }

  return (
    <nav className="pager" aria-label="Pagination">
      <span className="muted" style={{ fontSize: '0.82rem' }}>
        Showing{' '}
        <b className="num">
          {first}–{last}
        </b>{' '}
        of <b className="num">{page.total.toLocaleString('en-GB')}</b>
      </span>
      <div className="pager__pages">
        <button
          type="button"
          className="pnum"
          disabled={current <= 1}
          onClick={() => onGo(current - 1)}
          aria-label="Previous page"
        >
          <Icon name="prev" size={13} />
        </button>
        {numbers.map((n, index) =>
          n === 'gap' ? (
            <button
              type="button"
              className="pnum pnum--dots"
              disabled
              key={`gap-${index}`}
              aria-hidden="true"
            >
              …
            </button>
          ) : (
            <button
              type="button"
              key={n}
              className={`pnum${n === current ? ' is-on' : ''}`}
              onClick={() => onGo(n)}
              aria-current={n === current ? 'page' : undefined}
              aria-label={`Page ${n}`}
            >
              {n}
            </button>
          ),
        )}
        <button
          type="button"
          className="pnum"
          disabled={current >= total}
          onClick={() => onGo(current + 1)}
          aria-label="Next page"
        >
          <Icon name="next" size={13} />
        </button>
      </div>
    </nav>
  );
}
