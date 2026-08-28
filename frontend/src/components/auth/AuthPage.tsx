import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../../api/client';
import type { Auth } from '../../state/auth';

/**
 * The door. Nothing else in the product is reachable until this is satisfied.
 *
 * It replaces `AuthDialog`, which was a modal — and `DESIGN.md` refuses modals
 * for anything but the detail drawer, so that component was a violation of the
 * design system from the day it shipped. This is not merely "the dialog on a
 * page": a modal implies the thing behind it is still yours to look at, and
 * since D26 it is not.
 *
 * **The composition.** An asymmetric split, black plate against white form. The
 * centred card is the reflexive shape for a sign-in screen and it is exactly
 * what this should not be — the dashboard behind this door is a dense monochrome
 * console, and its cover plate ought to look like the front of an instrument
 * rather than a web form floating in space. The plate takes the larger share and
 * carries the identity; the form gets the quieter half and generous room.
 *
 * **The typography is the decoration**, because the palette has no colour to
 * spend and `DESIGN.md` refuses gradients, glass and ornament. The character
 * comes from range instead: a large wordmark tracked tight at -0.04em against
 * micro-labels tracked wide at 0.18em. Two extremes of one family, which is a
 * deliberately typographic answer to a monochrome constraint.
 *
 * Everything here obeys the existing tokens — no new colour, no new font, no new
 * dependency.
 */
export function AuthPage({ auth }: { auth: Auth }) {
  const canRegister = auth.bootstrap || auth.inviteToken !== null || auth.joinToken !== null;
  const [mode, setMode] = useState<'signin' | 'register'>(canRegister ? 'register' : 'signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const firstField = useRef<HTMLInputElement | null>(null);

  // The deployment can change under an open page: somebody else registers the
  // first account and registration closes. Re-deciding on `canRegister` rather
  // than only at mount stops this offering a form that would be refused.
  useEffect(() => {
    setMode(canRegister ? 'register' : 'signin');
  }, [canRegister]);

  useEffect(() => {
    setError(null);
    firstField.current?.focus();
  }, [mode]);

  const registering = mode === 'register';

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (registering) {
        await auth.register({ email, password, displayName });
      } else {
        await auth.signIn(email, password);
      }
      // No navigation and no reload: App swaps this page for the dashboard the
      // moment `auth.user` lands. A reload here would throw away the ?tender=
      // deep link that sent the reader to sign in in the first place.
    } catch (caught) {
      // The API writes these for the person reading them — an expired invite
      // says so; a wrong password deliberately does not say which half was
      // wrong. Re-wording them here would undo both.
      setError(caught instanceof ApiError ? caught.message : 'Something went wrong. Try again.');
      setBusy(false);
    }
  };

  return (
    <main className="gate">
      <section className="gate__plate" aria-hidden="true">
        <div className="gate__plateinner">
          <p className="gate__mark">TM</p>
          <h1 className="gate__wordmark">
            Tender
            <br />
            Monitor
          </h1>
          <p className="gate__strap">
            Public procurement, filtered down to the few notices worth a bid.
          </p>

          <dl className="gate__specimen">
            <div>
              <dt>Sources</dt>
              <dd>8 public feeds, no paid API</dd>
            </div>
            <div>
              <dt>Scoring</dt>
              <dd>Deterministic and explained</dd>
            </div>
            <div>
              <dt>Sweeps</dt>
              <dd>Twice daily, Asia/Dhaka</dd>
            </div>
          </dl>
        </div>
      </section>

      <section className="gate__form">
        {auth.invitationStatus === 'entering' ? (
          <EnteringDashboard auth={auth} />
        ) : auth.acceptToken && auth.invitationStatus !== 'dead' ? (
          <AcceptInvitation auth={auth} />
        ) : (
          <div className="gate__formInner">
            <p className="gate__eyebrow">
              {auth.bootstrap
                ? 'First account'
                : auth.invitationStatus === 'dead'
                  ? 'That link has expired'
                  : registering
                    ? auth.joinToken
                      ? 'Join this workspace'
                      : 'By invitation'
                    : 'Restricted dashboard'}
            </p>
            <h2 className="gate__title">{registering ? 'Create your account' : 'Sign in'}</h2>

            {/*
              A link that the API refused. Said here rather than behind a button
              press, because the lookup already knows — and because the only
              useful action is the one this sentence names.
            */}
            {auth.invitationStatus === 'dead' && auth.invitationError ? (
              <p className="gate__error" role="alert">
                {auth.invitationError}
              </p>
            ) : null}

            {auth.bootstrap ? (
              <p className="gate__note">
                No account exists yet. <b>This one becomes the administrator</b>, and everyone after
                you joins by invitation.
              </p>
            ) : registering && auth.joinToken ? (
              <p className="gate__note">
                Use the address your administrator added to this workspace. Any other address will
                be turned away.
              </p>
            ) : registering ? (
              <p className="gate__note">You are joining on an invitation.</p>
            ) : (
              <p className="gate__note gate__note--quiet">
                You need an account to view tenders. Nothing here is public.
              </p>
            )}

            <form className="gate__fields" onSubmit={submit}>
              {registering ? (
                <label className="field">
                  <span className="field__label">Your name</span>
                  <input
                    ref={registering ? firstField : undefined}
                    className="input"
                    type="text"
                    value={displayName}
                    autoComplete="name"
                    placeholder="How your name should appear"
                    onChange={(event) => setDisplayName(event.target.value)}
                  />
                </label>
              ) : null}

              <label className="field">
                <span className="field__label">Email</span>
                <input
                  ref={registering ? undefined : firstField}
                  className="input"
                  type="email"
                  required
                  value={email}
                  autoComplete="username"
                  onChange={(event) => setEmail(event.target.value)}
                />
              </label>

              <label className="field">
                <span className="field__label">Password</span>
                <input
                  className="input"
                  type="password"
                  required
                  value={password}
                  // A password manager needs to be told which of the two this is,
                  // or it offers a stored password to a registration form and
                  // saves the wrong thing.
                  autoComplete={registering ? 'new-password' : 'current-password'}
                  onChange={(event) => setPassword(event.target.value)}
                />
                {registering ? (
                  <small className="field__hint">
                    At least 10 characters. Length beats symbols.
                  </small>
                ) : null}
              </label>

              {error ? (
                <p className="gate__error" role="alert">
                  {error}
                </p>
              ) : null}

              <button type="submit" className="btn btn--primary gate__submit" disabled={busy}>
                {busy
                  ? registering
                    ? 'Creating…'
                    : 'Signing in…'
                  : registering
                    ? 'Create account'
                    : 'Sign in'}
              </button>
            </form>

            <p className="gate__foot">
              {registering ? (
                <button type="button" className="linkish" onClick={() => setMode('signin')}>
                  I already have an account
                </button>
              ) : canRegister ? (
                <button type="button" className="linkish" onClick={() => setMode('register')}>
                  Create an account
                </button>
              ) : (
                // Not a mode switch: without an invitation there is no
                // registration that could succeed, so this says what to do instead
                // rather than offering a form that will be refused.
                //
                // It also has to answer the person this form cannot help at all
                // (D31): somebody who joined by access link, never set a
                // password, and has just signed out. The form above is unusable
                // for them and says nothing about why, so the two ways back are
                // named here.
                <span className="gate__closed">
                  New accounts are by invitation. <b>No password?</b> Open your access link again,
                  or ask an administrator to set one for you.
                </span>
              )}
            </p>
          </div>
        )}
      </section>
    </main>
  );
}

/**
 * An administrator's link, being spent. Nothing to press (D30).
 *
 * The one screen in this product that exists purely to be brief. It is *not* a
 * blank frame, and that is the design decision: creating an account takes a
 * round trip, and a screen with the plate, their address and a verb on it says
 * "this is working" where an empty half-page says "this is broken". Same
 * composition as every other state, so nothing shifts when it is replaced.
 *
 * No spinner. `DESIGN.md` gives this surface one authored moment and it is the
 * detail drawer; the staggered reveal the gate already animates carries the
 * sense of motion, and `aria-busy` carries it to a screen reader.
 */
function EnteringDashboard({ auth }: { auth: Auth }) {
  return (
    <div className="gate__formInner" aria-busy="true">
      <p className="gate__eyebrow">Administrator</p>
      <h2 className="gate__title">Opening the dashboard</h2>
      <p className="gate__note">
        {auth.invitation ? (
          <>
            Signing you in as <b>{auth.invitation.email}</b>. There is nothing to accept — an
            administrator&rsquo;s link goes straight through.
          </>
        ) : (
          <>Signing you in.</>
        )}
      </p>
    </div>
  );
}

/**
 * A member's link: read who you are, press one button (D30).
 *
 * No fields, because there is nothing to collect — the link they followed *is*
 * the credential (D29), so their address and role are already known and a
 * password never enters the picture.
 *
 * **A member presses a button and an administrator does not, and that asymmetry
 * is the feature.** Somebody joining for the first time is told what they are
 * joining, by whose address, before anything happens; an administrator set the
 * workspace up and gains nothing from confirming a shape they authored. The
 * unfurl hazard D29 named is unaffected either way — a chat client fetches this
 * page's HTML and runs none of its JavaScript, so no preview reaches `/accept`.
 *
 * **The address is on screen because a wrong link is otherwise invisible.** Two
 * colleagues each forwarded the other's link would both join as the wrong
 * person and nobody would find out until the audit. Naming it turns that into
 * something a reader can see before they press.
 *
 * Degrades on purpose: with no `invitation` — the lookup could not be reached —
 * this falls back to exactly the screen it was before, one button and no claims
 * about who is holding the link.
 */
function AcceptInvitation({ auth }: { auth: Auth }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(auth.invitationError);

  const invitation = auth.invitation;
  const returning = invitation?.joined ?? false;
  const elsewhere = auth.invitationForSomebodyElse;

  const accept = async () => {
    setBusy(true);
    setError(null);
    try {
      await auth.acceptInvitation();
      // Nothing to navigate to: App swaps this page for the dashboard the
      // moment the user lands in state.
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Something went wrong. Try again.');
      setBusy(false);
    }
  };

  return (
    <div className="gate__formInner">
      <p className="gate__eyebrow">
        {elsewhere ? 'Somebody else’s link' : returning ? 'Welcome back' : 'You have been invited'}
      </p>
      <h2 className="gate__title">{returning ? 'Sign back in' : 'Join Tender Monitor'}</h2>

      {invitation ? (
        // A hairline row rather than a heading: it is a fact to check, not the
        // thing being said. Same shape as the plate's specimen list, which is
        // the one pattern this surface already uses for label-and-value.
        <dl className="gate__ident">
          <div>
            <dt>Link belongs to</dt>
            <dd>{invitation.email}</dd>
          </div>
          <div>
            <dt>You join as</dt>
            <dd>{invitation.role === 'admin' ? 'Administrator' : 'Member'}</dd>
          </div>
        </dl>
      ) : null}

      {elsewhere && auth.user ? (
        <p className="gate__note">
          You are signed in as <b>{auth.user.email}</b>, and this link is not yours. Accepting it
          switches this browser to {invitation?.email ?? 'its owner'} — and their link stays theirs
          to use.
        </p>
      ) : returning ? (
        <p className="gate__note">
          You already have an account here. <b>There is still no password</b> — press the button and
          this browser is signed in again.
        </p>
      ) : (
        <p className="gate__note">
          Press the button and you are in. <b>There is no password to set</b> — this link is yours,
          and it will sign you in again whenever you open it.
        </p>
      )}

      {error ? (
        <p className="gate__error" role="alert">
          {error}
        </p>
      ) : null}

      <button
        type="button"
        className="btn btn--primary gate__submit"
        disabled={busy}
        onClick={() => void accept()}
      >
        {busy
          ? returning
            ? 'Signing in…'
            : 'Joining…'
          : returning
            ? 'Continue to the dashboard'
            : 'Accept invitation'}
      </button>

      <p className="gate__foot">
        {elsewhere && auth.user ? (
          // The way out. Without it the only button on this page signs them out
          // of their own account and into a colleague's, and the only escape is
          // a reload they have to think of.
          <button type="button" className="linkish" onClick={auth.dismissInvitation}>
            Not now — stay signed in as {auth.user.email}
          </button>
        ) : (
          <span className="gate__closed">
            Keep the link. Opening it on another device signs you in there too.
          </span>
        )}
      </p>
    </div>
  );
}
