import { useEffect, useState } from 'react';
import type { MatchingProfile, MatchingRules, RulesPreview } from '../types';
import { api } from '../api/client';
import { Icon } from './Icon';
import { PhraseTable } from './settings/PhraseTable';
import { SettingsPage } from './settings/SettingsPage';

const WEIGHTS: { key: string; label: string; hint: string }[] = [
  { key: 'topic', label: 'Topic', hint: 'How much the subject matter matters' },
  { key: 'product_fit', label: 'Product fit', hint: 'Cloud vs on-premises, and what we sell' },
  { key: 'procurement_intent', label: 'Procurement intent', hint: 'Whether it reads as a real buy' },
];

const BANDS: { key: string; label: string }[] = [
  { key: 'excellent_fit', label: 'Excellent fit' },
  { key: 'good_fit', label: 'Good fit' },
  { key: 'possible_fit', label: 'Possible fit' },
  { key: 'weak_fit', label: 'Weak fit' },
];

/**
 * The ~15% of relevance_profiles.yaml people actually tune.
 *
 * Regexes stay in the file: they are the sharp edge and rarely touched. Saving
 * re-scores every stored notice, so it asks first — someone may have been
 * working a shortlist for a week under the old ranking.
 */
export function MatchingRulesSettings({
  onBack,
  onRescored,
}: {
  onBack: () => void;
  onRescored: () => void;
}) {
  const [rules, setRules] = useState<MatchingRules | null>(null);
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [bands, setBands] = useState<Record<string, number>>({});
  const [profiles, setProfiles] = useState<MatchingProfile[]>([]);
  const [openProfile, setOpenProfile] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<RulesPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [message, setMessage] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);

  useEffect(() => {
    let live = true;
    void api
      .matchingRules()
      .then((data) => {
        if (!live) return;
        setRules(data);
        setWeights(data.weights);
        setBands(data.bands);
        setProfiles(data.profiles);
      })
      .catch((error: unknown) =>
        setMessage({ tone: 'bad', text: error instanceof Error ? error.message : 'Could not load.' }),
      );
    return () => {
      live = false;
    };
  }, []);

  const total = WEIGHTS.reduce((sum, w) => sum + (weights[w.key] ?? 0), 0);
  const balanced = Math.abs(total - 1) < 0.005;

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const asProfiles = Object.fromEntries(
        profiles.map((p) => [p.key, { strong: p.strong, medium: p.medium, weak: p.weak }]),
      );
      const result = await api.saveMatchingRules({ weights, bands, profiles: asProfiles });
      setMessage({
        tone: 'ok',
        text: `Saved. ${result.rescored.toLocaleString('en-GB')} notices re-scored under the new rules.`,
      });
      setConfirming(null);
      onRescored();
    } catch (error) {
      setMessage({ tone: 'bad', text: error instanceof Error ? error.message : 'Could not save.' });
    } finally {
      setBusy(false);
    }
  };

  const askFirst = async () => {
    setPreviewing(true);
    setMessage(null);
    try {
      const asProfiles = Object.fromEntries(
        profiles.map((p) => [p.key, { strong: p.strong, medium: p.medium, weak: p.weak }]),
      );
      setConfirming(await api.previewMatchingRules({ weights, bands, profiles: asProfiles }));
    } catch (error) {
      setMessage({
        tone: 'bad',
        text: error instanceof Error ? error.message : 'Could not work out what would change.',
      });
    } finally {
      setPreviewing(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    try {
      const result = await api.resetMatchingRules();
      const fresh = await api.matchingRules();
      setRules(fresh);
      setWeights(fresh.weights);
      setBands(fresh.bands);
      setProfiles(fresh.profiles);
      setMessage({
        tone: 'ok',
        text: `Back to the file's defaults. ${result.rescored.toLocaleString('en-GB')} notices re-scored.`,
      });
      onRescored();
    } catch (error) {
      setMessage({ tone: 'bad', text: error instanceof Error ? error.message : 'Could not reset.' });
    } finally {
      setBusy(false);
    }
  };

  if (!rules) {
    return (
      <SettingsPage title="Matching rules" blurb="How a notice earns its score." onBack={onBack}>
        {message ? (
          <p className="notice notice--bad" role="status">
            {message.text}
          </p>
        ) : (
          <p className="screen__foot">Loading…</p>
        )}
      </SettingsPage>
    );
  }

  return (
    <SettingsPage
      title="Matching rules"
      blurb="How a notice earns its score. Phrase lists and regexes live in config/relevance_profiles.yaml; what you change here is stored separately and merged over the file, so the file stays readable and resetting is one click."
      onBack={onBack}
    >

      {message ? (
        <p className={`notice${message.tone === 'bad' ? ' notice--bad' : ' notice--ok'}`} role="status">
          {message.text}
        </p>
      ) : null}

      <h3 className="screen__section">Weights</h3>
      <p className="screen__hint">
        The three must add up to 1.00. Currently <b>{total.toFixed(2)}</b>.
      </p>
      <ul className="tunelist">
        {WEIGHTS.map((weight) => (
          <li key={weight.key}>
            <label htmlFor={`w-${weight.key}`}>
              {weight.label}
              <span>{weight.hint}</span>
            </label>
            <input
              id={`w-${weight.key}`}
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={weights[weight.key] ?? 0}
              onChange={(event) =>
                setWeights((prev) => ({ ...prev, [weight.key]: Number(event.target.value) }))
              }
            />
            <output>{(weights[weight.key] ?? 0).toFixed(2)}</output>
          </li>
        ))}
      </ul>

      <h3 className="screen__section">Score bands</h3>
      <p className="screen__hint">
        Where each fit label starts. They must descend — a band above the one before it would put a
        notice in two at once.
      </p>
      <ul className="tunelist">
        {BANDS.map((band) => (
          <li key={band.key}>
            <label htmlFor={`b-${band.key}`}>{band.label}</label>
            <input
              id={`b-${band.key}`}
              className="input input--sm"
              type="number"
              min={0}
              max={100}
              value={bands[band.key] ?? 0}
              onChange={(event) =>
                setBands((prev) => ({ ...prev, [band.key]: Number(event.target.value) }))
              }
            />
          </li>
        ))}
      </ul>

      <h3 className="screen__section">Capability phrases</h3>
      <p className="screen__hint">
        What the engine looks for in a notice. A hit scores <b>strong 26</b>, <b>medium 12</b> or{' '}
        <b>weak 5</b> points toward that profile, and <b>1.9×</b> as much when it lands in the title
        rather than the description. Those points make the topic score, which is 55% of the final
        one.
      </p>
      <ul className="proflist">
        {profiles.map((profile) => {
          const open = openProfile === profile.key;
          return (
            <li key={profile.key} className={open ? 'is-open' : undefined}>
              <button
                type="button"
                className="proflist__head"
                aria-expanded={open}
                onClick={() => setOpenProfile(open ? null : profile.key)}
              >
                <b>{profile.label}</b>
                <span>
                  {profile.strong.length} strong · {profile.medium.length} medium ·{' '}
                  {profile.weak.length} weak
                </span>
                <Icon name="chevronDown" size={14} />
              </button>
              {open ? (
                <PhraseTable
                  profile={profile}
                  onChange={(next) =>
                    setProfiles((prev) => prev.map((p) => (p.key === next.key ? next : p)))
                  }
                />
              ) : null}
            </li>
          );
        })}
      </ul>

      <div className="screen__actions">
        {confirming ? (
          <>
            <p className="screen__warn">
              {confirming.sampled ? 'About ' : ''}
              <b>{confirming.changed.toLocaleString('en-GB')}</b> of{' '}
              {confirming.examined.toLocaleString('en-GB')} notices change score ·{' '}
              <b>{confirming.crossing_up}</b> rise into Top scoring ·{' '}
              <b>{confirming.crossing_down}</b> drop out of it.
              {confirming.changed === 0
                ? ' Nothing moves — saving only records the new rules.'
                : ' Anyone working from the current ordering will see it change.'}
            </p>
            <button type="button" className="btn btn--primary" disabled={busy} onClick={() => void save()}>
              {busy ? 'Re-scoring…' : 'Save and re-score'}
            </button>
            <button type="button" className="btn btn--ghost" onClick={() => setConfirming(null)}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy || previewing || !balanced}
              title={balanced ? undefined : 'The weights must add up to 1.00'}
              onClick={() => void askFirst()}
            >
              {previewing ? 'Checking…' : 'Save changes'}
            </button>
            {rules.overridden.length > 0 ? (
              <button type="button" className="btn" disabled={busy} onClick={() => void reset()}>
                Reset to file defaults
              </button>
            ) : null}
          </>
        )}
      </div>
    </SettingsPage>
  );
}
