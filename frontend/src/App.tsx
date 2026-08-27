import { AuthPage } from './components/auth/AuthPage';
import { Dashboard } from './pages/Dashboard';
import { useAuth } from './state/auth';

/**
 * The gate (D26). Nothing renders until we know who is asking.
 *
 * The Dashboard is not merely *hidden* when signed out — it is not mounted at
 * all. That matters for a reason beyond tidiness: it fires its own loads on
 * mount, so rendering it behind an overlay would fire a dozen requests that all
 * 401, fill the console with red, and briefly paint counts and headings that the
 * reader is not entitled to. Mounting is the switch, not CSS.
 *
 * The gate is real on the server as well. Hiding these pages while
 * `GET /api/tenders` still answered anybody would be theatre; see
 * `app/security.py::enforce_sign_in`.
 */
export default function App() {
  const auth = useAuth();

  // The first reply has not landed. A blank frame rather than a spinner: this
  // resolves in a few milliseconds on a LAN, and a spinner that flashes for
  // 40ms reads as jank, while a sign-in form that flashes before the session
  // resolves reads as being logged out.
  if (auth.status === 'loading') {
    return <div className="gate__wait" aria-busy="true" aria-label="Loading" />;
  }

  // An API that cannot be reached is not "signed out", and offering a sign-in
  // form here would be offering a button that can only fail.
  if (auth.status === 'unreachable') {
    return (
      <main className="gate gate--flat">
        <div className="gate__formInner">
          <p className="gate__eyebrow">No connection</p>
          <h2 className="gate__title">Cannot reach the API</h2>
          <p className="gate__note gate__note--quiet">
            The dashboard is running but the backend did not answer. If this is the hosted
            deployment it may still be starting.
          </p>
          <button
            type="button"
            className="btn btn--primary gate__submit"
            onClick={() => void auth.refresh()}
          >
            Try again
          </button>
        </div>
      </main>
    );
  }

  // Narrowed here so the Dashboard and everything under it can take a plain
  // `User` rather than `User | null` and a trail of non-null assertions.
  if (!auth.user) return <AuthPage auth={auth} />;

  return <Dashboard auth={auth} user={auth.user} />;
}
