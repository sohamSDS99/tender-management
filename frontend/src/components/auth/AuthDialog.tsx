import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../../api/client';
import type { Auth } from '../../state/auth';
import { Icon } from '../Icon';

/**
 * Signing in and creating an account, in one dialog with two modes.
 *
 * A dialog rather than a page, and that is the deliberate part: this product
 * reads perfectly well signed out (D25), so sending somebody to a full-page
 * sign-in screen would take away the thing they came for in order to offer them
 * something optional. The results stay behind the scrim, and closing the dialog
 * returns them to exactly what they were reading.
 *
 * Which mode opens first is decided by the deployment, not by a coin toss:
 *
 * * **Nobody has registered yet** — registration opens, and says out loud that
 *   this account will be the administrator. That is the single most consequential
 *   moment in the whole feature and it must not look like an ordinary signup.
 * * **The reader followed an invitation link** — registration opens, with the
 *   token already held in state.
 * * **Otherwise** — sign-in opens, because registration is closed to anyone
 *   without an invite and offering a form that cannot succeed is worse than not
 *   offering it.
 */
export function AuthDialog({
  auth,
  open,
  onClose,
}: {
  auth: Auth;
  open: boolean;
  onClose: () => void;
}) {
  const canRegister = auth.bootstrap || auth.inviteToken !== null;
  const [mode, setMode] = useState<'signin' | 'register'>(canRegister ? 'register' : 'signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const firstField = useRef<HTMLInputElement | null>(null);

  // The deployment can change under an open page — the first admin registers,
  // and now registration is closed. Re-deciding on `canRegister` rather than
  // only at mount stops the dialog offering a form that would be refused.
  useEffect(() => {
    if (open) setMode(canRegister ? 'register' : 'signin');
  }, [open, canRegister]);

  useEffect(() => {
    if (!open) return;
    setError(null);
    // Focus after paint: the element does not exist while the dialog is shut.
    const timer = window.setTimeout(() => firstField.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [open, mode]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

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
      setEmail('');
      setPassword('');
      setDisplayName('');
      onClose();
    } catch (caught) {
      // The API writes these for the person reading them — an invite that has
      // expired says so, a wrong password deliberately does not say which half
      // was wrong. Re-wording them here would undo both.
      setError(caught instanceof ApiError ? caught.message : 'Something went wrong. Try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="scrim is-on" onClick={onClose} />
      <div
        className="authdlg"
        role="dialog"
        aria-modal="true"
        aria-labelledby="authdlg-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="authdlg__head">
          <h2 id="authdlg-title">{registering ? 'Create your account' : 'Sign in'}</h2>
          <button type="button" className="btn btn--icon" onClick={onClose} title="Close">
            <Icon name="close" size={15} />
          </button>
        </header>

        {auth.bootstrap ? (
          <p className="authdlg__note authdlg__note--first">
            <b>This is the first account on this dashboard.</b> It becomes the administrator, and
            everyone after you joins by invitation.
          </p>
        ) : null}

        {registering && auth.inviteToken ? (
          <p className="authdlg__note">You are joining on an invitation.</p>
        ) : null}

        <form className="authdlg__form" onSubmit={submit}>
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
              // Tells a password manager to offer a new one when registering and
              // a stored one when signing in. The same value for both is what
              // makes managers save the wrong thing.
              autoComplete={registering ? 'new-password' : 'current-password'}
              onChange={(event) => setPassword(event.target.value)}
            />
            {registering ? (
              <small className="field__hint">At least 10 characters. Length beats symbols.</small>
            ) : null}
          </label>

          {error ? (
            <p className="authdlg__error" role="alert">
              {error}
            </p>
          ) : null}

          <button type="submit" className="btn btn--primary" disabled={busy}>
            {busy
              ? registering
                ? 'Creating…'
                : 'Signing in…'
              : registering
                ? 'Create account'
                : 'Sign in'}
          </button>
        </form>

        <footer className="authdlg__foot">
          {registering ? (
            <button type="button" className="linkish" onClick={() => setMode('signin')}>
              I already have an account
            </button>
          ) : canRegister ? (
            <button type="button" className="linkish" onClick={() => setMode('register')}>
              Create an account
            </button>
          ) : (
            // Not a form, and not a mode switch: without an invite there is no
            // registration that could succeed, so this says what to do instead.
            <span className="authdlg__closed">
              New accounts are by invitation. Ask an administrator for a link.
            </span>
          )}
        </footer>
      </div>
    </>
  );
}
