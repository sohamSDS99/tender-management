import type { DeploymentFit, FitStatus } from '../types';
import { DEPLOYMENT_LABELS, DEPLOYMENT_TONE, FIT_LABELS, FIT_TONE, scoreTone } from '../labels';

export function ScorePill({ score }: { score: number }) {
  return (
    <span className={`score score--${scoreTone(score)}`} title={`Relevance score ${score}/100`}>
      {score}
    </span>
  );
}

export function FitBadge({ fit }: { fit: FitStatus }) {
  return <span className={`badge badge--${FIT_TONE[fit]}`}>{FIT_LABELS[fit]}</span>;
}

export function DeploymentBadge({ deployment }: { deployment: DeploymentFit }) {
  return (
    <span className={`badge badge--${DEPLOYMENT_TONE[deployment]}`}>
      {DEPLOYMENT_LABELS[deployment]}
    </span>
  );
}

export function Tag({ children }: { children: React.ReactNode }) {
  return <span className="tag">{children}</span>;
}
