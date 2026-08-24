import type { TenderPage } from '../types';
import { Icon } from './Icon';

/**
 * Numbered pages with a window around the current one.
 *
 * The window exists because 1,284 stored notices at 25 a page is 52 pages, and
 * prev/next alone makes page 40 a forty-click journey. Ellipses are rendered as
 * inert spans rather than disabled buttons so they are not tab stops.
 */
function pageWindow(current: number, total: number): (number | 'gap')[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages = new Set<number>([1, total, current]);
  for (const offset of [-1, 1]) {
    const near = current + offset;
    if (near > 1 && near < total) pages.add(near);
  }
  // Keep the row a stable width near the ends, so it does not reflow as you page.
  if (current <= 3) [2, 3, 4].forEach((p) => p < total && pages.add(p));
  if (current >= total - 2) [total - 3, total - 2, total - 1].forEach((p) => p > 1 && pages.add(p));

  const sorted = [...pages].filter((p) => p >= 1 && p <= total).sort((a, b) => a - b);
  const out: (number | 'gap')[] = [];
  sorted.forEach((value, index) => {
    if (index > 0 && value - sorted[index - 1] > 1) out.push('gap');
    out.push(value);
  });
  return out;
}

export function Pager({ page, onGo }: { page: TenderPage; onGo: (next: number) => void }) {
  if (page.pages <= 1) return null;
  const first = (page.page - 1) * page.page_size + 1;
  const last = Math.min(page.page * page.page_size, page.total);

  return (
    <nav className="pager" aria-label="Pagination">
      <span className="muted num">
        {first.toLocaleString('en-GB')}–{last.toLocaleString('en-GB')} of{' '}
        {page.total.toLocaleString('en-GB')}
      </span>
      <div className="pager__pages">
        <button
          type="button"
          className="pnum"
          disabled={page.page <= 1}
          aria-label="Previous page"
          onClick={() => onGo(page.page - 1)}
        >
          <Icon name="prev" size={13} />
        </button>

        {pageWindow(page.page, page.pages).map((entry, index) =>
          entry === 'gap' ? (
            <span className="pnum pnum--dots" key={`gap-${index}`} aria-hidden="true">
              …
            </span>
          ) : (
            <button
              type="button"
              key={entry}
              className={`pnum${entry === page.page ? ' is-on' : ''}`}
              aria-label={`Page ${entry}`}
              aria-current={entry === page.page ? 'page' : undefined}
              onClick={() => onGo(entry)}
            >
              {entry}
            </button>
          ),
        )}

        <button
          type="button"
          className="pnum"
          disabled={page.page >= page.pages}
          aria-label="Next page"
          onClick={() => onGo(page.page + 1)}
        >
          <Icon name="next" size={13} />
        </button>
      </div>
    </nav>
  );
}
