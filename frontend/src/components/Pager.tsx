import type { TenderPage } from '../types';
import { Icon } from './Icon';

/** Prev/next plus a position readout. Numbered pages are noise at this scale. */
export function Pager({ page, onGo }: { page: TenderPage; onGo: (next: number) => void }) {
  if (page.pages <= 1) return null;
  const first = (page.page - 1) * page.page_size + 1;
  const last = Math.min(page.page * page.page_size, page.total);

  return (
    <nav className="pager" aria-label="Pagination">
      <span>
        {first.toLocaleString('en-GB')}–{last.toLocaleString('en-GB')} of{' '}
        {page.total.toLocaleString('en-GB')}
      </span>
      <div className="pager__btns">
        <button
          type="button"
          className="btn"
          disabled={page.page <= 1}
          onClick={() => onGo(page.page - 1)}
        >
          <Icon name="prev" size={13} />
          Previous
        </button>
        <button
          type="button"
          className="btn"
          disabled={page.page >= page.pages}
          onClick={() => onGo(page.page + 1)}
        >
          Next
          <Icon name="next" size={13} />
        </button>
      </div>
    </nav>
  );
}
