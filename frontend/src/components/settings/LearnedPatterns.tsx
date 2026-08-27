import { useEffect, useState } from 'react';
import { ApiError, api } from '../../api/client';
import type { LearnedModel } from '../../types';
import { Icon } from '../Icon';

/**
 * What the system worked out for itself, and the evidence for each conclusion.
 *
 * This screen is the price of being allowed to hide anything. The phrase lists
 * above it were written by a person and can be argued with by reading them; a
 * learned pattern cannot, unless it is shown along with how many rejections it
 * appears in and how many other notices it does not. So it is shown. A wrong
 * pattern is traced back to the mark that produced it and that mark withdrawn -
 * which is why every verdict is reversible from the Not relevant view.
 *
 * It reads and renders. There is nothing to save: the model is derived from the
 * verdicts, so the only way to change it is to change one of those.
 */
export function LearnedPatterns() {
  const [model, setModel] = useState<LearnedModel | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void api
      .learned()
      .then((data) => live && (setModel(data), setError(null)))
      .catch((err) => live && setError(err instanceof ApiError ? err.message : String(err)));
    return () => {
      live = false;
    };
  }, []);

  if (error) {
    return (
      <p className="notice notice--bad" role="status">
        <Icon name="warn" size={13} />
        Could not read what has been learned: {error}
      </p>
    );
  }
  if (!model) return <p className="screen__hint">Loading…</p>;

  const { marks_irrelevant: rejected, marks_relevant: kept, marks_needed: needed } = model;

  return (
    <>
      <h3 className="screen__section">Learned from your marks</h3>
      <p className="screen__hint">
        Nobody writes these. They are the phrases concentrated in the notices marked{' '}
        <b>not relevant</b> and rare everywhere else, so a word common to both — “contract”,
        “services” — earns no weight and drops out on its own. None of this can move a relevance
        score; it decides only whether a notice is shown.
      </p>

      <p className="screen__hint">
        <b>{rejected.toLocaleString('en-GB')}</b> marked not relevant ·{' '}
        <b>{kept.toLocaleString('en-GB')}</b> marked relevant · across{' '}
        <b>{model.corpus.toLocaleString('en-GB')}</b> stored notices.{' '}
        {model.hidden_total > 0 ? (
          <>
            <b>{model.hidden_total.toLocaleString('en-GB')}</b> hidden in total —{' '}
            {model.hidden_by_hand.toLocaleString('en-GB')} by hand and{' '}
            {model.hidden_by_learning.toLocaleString('en-GB')} by these patterns.
          </>
        ) : (
          'Nothing is hidden.'
        )}
      </p>

      {!model.active ? (
        <p className="notice" role="status">
          <Icon name="info" size={13} />
          {needed > 0
            ? `Not yet acting on its own. ${needed} more ${needed === 1 ? 'notice' : 'notices'} marked not relevant and it will start hiding ones that look like them. Until then only the notices you mark are hidden.`
            : 'No pattern has cleared its floor yet, so nothing is being hidden automatically. A phrase has to appear in at least three separate rejections and be markedly rarer elsewhere.'}
        </p>
      ) : null}

      {model.patterns.length > 0 ? (
        <table className="ptable">
          <thead>
            <tr>
              <th scope="col">Pattern</th>
              <th scope="col">In rejections</th>
              <th scope="col">Elsewhere</th>
              <th scope="col">Weight</th>
            </tr>
          </thead>
          <tbody>
            {model.patterns.map((pattern) => (
              <tr key={pattern.phrase}>
                <td>{pattern.phrase}</td>
                {/* The two numbers are the argument. A pattern with 6 and 0 is
                    a real signal; one with 3 and 40 would be a bad rule, and
                    seeing it is how somebody catches it. */}
                <td className="num">{pattern.marked}</td>
                <td className="num">{pattern.elsewhere}</td>
                <td className="num">{pattern.weight.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="ptable__empty">
          No patterns yet. Mark a few notices not relevant and they will appear here.
        </p>
      )}

      <p className="screen__hint">
        A pattern that looks wrong is a mark that was wrong. Open <b>Not relevant</b> in the left
        rail, find the notice and withdraw it — the patterns are recomputed from what is left.
      </p>
    </>
  );
}
