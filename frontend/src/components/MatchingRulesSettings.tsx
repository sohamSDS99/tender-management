import { Fragment, useEffect, useState } from 'react';
import type { MatchingProfile, MatchingRules, RulesPreview } from '../types';
import { api } from '../api/client';
import { Icon } from './Icon';
import { LearnedPatterns } from './settings/LearnedPatterns';
import { PhraseTable } from './settings/PhraseTable';
import { SettingsPage } from './settings/SettingsPage';

const WEIGHTS: { key: string; label: string; hint: string }[] = [
  { key: 'topic', label: 'Topic', hint: 'How much the subject matter matters' },
  { key: 'product_fit', label: 'Product fit', hint: 'Cloud vs on-premises, and what we sell' },
  {
    key: 'procurement_intent',
    label: 'Procurement intent',
    hint: 'Whether it reads as a real buy',
  },
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
  const [removed, setRemoved] = useState<string[]>([]);
  const [fileProfiles, setFileProfiles] = useState<string[]>([]);
  const [newProfile, setNewProfile] = useState('');
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
        setRemoved(data.removed_profiles ?? []);
        setFileProfiles(data.file_profiles ?? []);
      })
      .catch((error: unknown) =>
        setMessage({
          tone: 'bad',
          text: error instanceof Error ? error.message : 'Could not load.',
        }),
      );
    return () => {
      live = false;
    };
  }, []);

  const slug = (value: string) =>
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 64);

  const newKey = slug(newProfile);
  const canAddProfile =
    newKey.length > 1 && /^[a-z]/.test(newKey) && !profiles.some((p) => p.key === newKey);

  const addProfile = () => {
    if (!canAddProfile) return;
    setProfiles((prev) => [
      ...prev,
      { key: newKey, label: newProfile.trim(), strong: [], medium: [], weak: [] },
    ]);
    setRemoved((prev) => prev.filter((k) => k !== newKey));
    setOpenProfile(newKey);
    setNewProfile('');
  };

  const removeProfile = (key: string) => {
    setProfiles((prev) => prev.filter((p) => p.key !== key));
    // Only a profile the file defines needs a tombstone; one added here just
    // stops being sent.
    if (fileProfiles.includes(key)) setRemoved((prev) => [...new Set([...prev, key])]);
    if (openProfile === key) setOpenProfile(null);
  };

  const restoreProfile = (key: string) => setRemoved((prev) => prev.filter((k) => k !== key));

  const restorable = removed.filter((key) => fileProfiles.includes(key));

  const total = WEIGHTS.reduce((sum, w) => sum + (weights[w.key] ?? 0), 0);
  const balanced = Math.abs(total - 1) < 0.005;

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const asProfiles = Object.fromEntries(
        profiles.map((p) => [
          p.key,
          { label: p.label, strong: p.strong, medium: p.medium, weak: p.weak },
        ]),
      );
      const result = await api.saveMatchingRules({
        weights,
        bands,
        profiles: asProfiles,
        removed_profiles: removed,
      });
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
        profiles.map((p) => [
          p.key,
          { label: p.label, strong: p.strong, medium: p.medium, weak: p.weak },
        ]),
      );
      setConfirming(
        await api.previewMatchingRules({
          weights,
          bands,
          profiles: asProfiles,
          removed_profiles: removed,
        }),
      );
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
      setRemoved(fresh.removed_profiles ?? []);
      setFileProfiles(fresh.file_profiles ?? []);
      setMessage({
        tone: 'ok',
        text: `Back to the file's defaults. ${result.rescored.toLocaleString('en-GB')} notices re-scored.`,
      });
      onRescored();
    } catch (error) {
      setMessage({
        tone: 'bad',
        text: error instanceof Error ? error.message : 'Could not reset.',
      });
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
        <p
          className={`notice${message.tone === 'bad' ? ' notice--bad' : ' notice--ok'}`}
          role="status"
        >
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
      <div className="phrases__add">
        <input
          className="input input--sm"
          value={newProfile}
          placeholder="New capability, e.g. Waste management"
          aria-label="New capability name"
          onChange={(event) => setNewProfile(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              addProfile();
            }
          }}
        />
        <button
          type="button"
          className="btn btn--primary btn--sm"
          disabled={!canAddProfile}
          title={canAddProfile ? undefined : 'Give it a name that is not already used'}
          onClick={addProfile}
        >
          Add capability
        </button>
      </div>

      <table className="ptable ptable--profiles">
        <thead>
          <tr>
            <th scope="col">Capability</th>
            <th scope="col">Phrases</th>
            <th scope="col" className="ptable__act">
              <span className="sr">Remove</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {profiles.map((profile) => {
            const open = openProfile === profile.key;
            return (
              <Fragment key={profile.key}>
                <tr>
                  <td>
                    <button
                      type="button"
                      className="proflist__head"
                      aria-expanded={open}
                      onClick={() => setOpenProfile(open ? null : profile.key)}
                    >
                      <Icon name="chevronDown" size={13} />
                      <b>{profile.label}</b>
                    </button>
                  </td>
                  <td className="mono ptable__counts">
                    {profile.strong.length} strong · {profile.medium.length} medium ·{' '}
                    {profile.weak.length} weak
                  </td>
                  <td className="ptable__act">
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      aria-label={`Remove capability: ${profile.label}`}
                      onClick={() => removeProfile(profile.key)}
                    >
                      <Icon name="close" size={13} />
                    </button>
                  </td>
                </tr>
                {open ? (
                  <tr className="ptable__drawer">
                    <td colSpan={3}>
                      <PhraseTable
                        profile={profile}
                        onChange={(next) =>
                          setProfiles((prev) => prev.map((p) => (p.key === next.key ? next : p)))
                        }
                      />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>

      {/* A profile from the file is switched off, not deleted — the file is
          never rewritten, so offering it back costs nothing and losing it
          silently would be the worse failure. */}
      {restorable.length > 0 ? (
        <p className="screen__hint">
          Switched off:{' '}
          {restorable.map((key, index) => (
            <Fragment key={key}>
              {index > 0 ? ', ' : ''}
              <button type="button" className="linkish" onClick={() => restoreProfile(key)}>
                {key.replace(/_/g, ' ')}
              </button>
            </Fragment>
          ))}
          . These come from the relevance file and can be brought back.
        </p>
      ) : null}

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
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy}
              onClick={() => void save()}
            >
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

      {/* Below the save button, deliberately: everything above is a rule
          somebody writes and saves, and this is a rule the system derived. It
          belongs on this page because it is also matching, and it has no save
          of its own because the only way to change it is to change a verdict. */}
      <LearnedPatterns />
    </SettingsPage>
  );
}
