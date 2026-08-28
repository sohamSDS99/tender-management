import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, auth as authApi, setUnauthorizedHandler } from '../api/client';
import type { Invitation, SessionState, User, UserRole } from '../types';

/**
 * Who is signed in, held the same way every other piece of state here is held:
 * a plain hook owned by the Dashboard and passed down as props. No context, for
 * the same reason `usePreferences` is not one — there is one page, and a
 * provider would add an indirection with nothing on the other side of it.
 *
 * The important property is that **signed out is not an error state**. The
 * dashboard reads tenders without an account (D25), so `user: null` is an
 * ordinary answer and there is no loading gate, no redirect and no error
 * banner attached to it. `status` exists only so the account control can avoid
 * flashing "Sign in" for the fraction of a second before the first reply lands.
 */

/** The invite token in `?invite=…`, if the reader arrived on an invitation. */
export function inviteFromSearch(search: string): string | null {
  return tokenFromSearch(search, 'invite');
}

/**
 * The workspace join token in `?join=…` (D28).
 *
 * Unlike an invitation this one is shared by the whole team and is reusable, so
 * several people arrive holding the same value. It still gets stripped from the
 * address bar: it is not a secret worth much on its own — the roster is what
 * decides — but leaving it in the URL means it ends up in screenshots and in
 * whatever somebody pastes when they later share "the dashboard link", and a
 * reader who has already joined has no use for it.
 */
export function joinFromSearch(search: string): string | null {
  return tokenFromSearch(search, 'join');
}

/**
 * A personal access token in `?accept=…` (D29).
 *
 * This one *is* the credential: opening the link and confirming signs the
 * holder in, with no password at any point. It is stripped from the address bar
 * as soon as it is read — for this token that matters far more than for the
 * others, because leaving it in a URL leaves somebody's whole account in their
 * browser history and in any screenshot of the page.
 */
export function acceptFromSearch(search: string): string | null {
  return tokenFromSearch(search, 'accept');
}

function tokenFromSearch(search: string, key: string): string | null {
  const raw = new URLSearchParams(search).get(key);
  return raw && raw.trim() ? raw.trim() : null;
}

/**
 * The same query string with `invite` removed.
 *
 * The token is a live credential, and leaving it in the address bar means it is
 * in the reader's history, in any screenshot of the page, and in whatever they
 * paste when they later share "the dashboard link". It is read once into state
 * and then taken out of the URL.
 */
export function withoutInvite(search: string): string {
  const params = new URLSearchParams(search);
  params.delete('invite');
  params.delete('join');
  params.delete('accept');
  return params.toString();
}

/**
 * Up to two letters for the account chip.
 *
 * Falls back through the name's words, then the name, then the address, because
 * every one of those can be a single character and an empty chip reads as a
 * rendering fault rather than as a person with a short name.
 */
export function initials(user: Pick<User, 'display_name' | 'email'> | null): string {
  if (!user) return '';
  const words = user.display_name.trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[words.length - 1][0]).toUpperCase();
  const source = words[0] || user.email;
  return source.slice(0, 2).toUpperCase();
}

/**
 * A browser's user-agent, shortened to something a person can recognise.
 *
 * The session list exists so somebody can answer "is that laptop still signed
 * in?", and a 140-character UA string answers it worse than "Safari on macOS"
 * does. Deliberately coarse: this is a recognition aid, not analytics, and a
 * wrong guess costs nothing because the raw string is in the title attribute.
 */
export function describeAgent(userAgent: string): string {
  if (!userAgent.trim()) return 'Unknown browser';
  const browser = /Edg\//.test(userAgent)
    ? 'Edge'
    : /OPR\//.test(userAgent)
      ? 'Opera'
      : /Firefox\//.test(userAgent)
        ? 'Firefox'
        : /Chrome\//.test(userAgent)
          ? 'Chrome'
          : /Safari\//.test(userAgent)
            ? 'Safari'
            : null;
  const platform = /iPhone|iPad/.test(userAgent)
    ? 'iOS'
    : /Android/.test(userAgent)
      ? 'Android'
      : /Mac OS X|Macintosh/.test(userAgent)
        ? 'macOS'
        : /Windows/.test(userAgent)
          ? 'Windows'
          : /Linux/.test(userAgent)
            ? 'Linux'
            : null;
  if (browser && platform) return `${browser} on ${platform}`;
  if (browser) return browser;
  if (platform) return platform;
  return userAgent.slice(0, 40);
}

/**
 * Whether opening a link should enter the dashboard with no click (D30).
 *
 * The rule the whole feature turns on, in one place so there is nothing to
 * disagree with it: **an administrator lands in the dashboard, a member lands on
 * the accept screen.** Administrators are the people who hand out links and set
 * up the workspace; making them confirm an invitation they issued the shape of
 * is a step with nothing behind it. A member is joining something for the first
 * time and gets told what they are joining before it happens.
 *
 * This does not weaken "accepting is a POST, never a GET" (D29). A chat client
 * unfurling the URL fetches the page's HTML and runs none of its JavaScript, so
 * no preview can reach `/accept`. The click that is skipped here is skipped by a
 * real browser running the app, which is a person opening their own link.
 */
export function landsStraightInDashboard(role: UserRole): boolean {
  return role === 'admin';
}

export type AuthStatus = 'loading' | 'ready' | 'unreachable';

/**
 * How far along the arrival-on-a-link journey this browser is.
 *
 * `checking` exists to stop a flash: without it a signed-in reader would see the
 * dashboard for a frame before being moved to an accept screen, and a signed-out
 * one would see the sign-in form before the invitation resolved.
 *
 * `entering` is the administrator's path — the lookup said `admin`, so `accept`
 * is already in flight and there is nothing for them to press.
 */
export type InvitationStatus = 'none' | 'checking' | 'entering' | 'ready' | 'dead';

export interface Auth {
  status: AuthStatus;
  user: User | null;
  /** True while no account exists: the next registration becomes the admin. */
  bootstrap: boolean;
  inviteRequired: boolean;
  /** Present when the reader arrived on a single-use invitation link. */
  inviteToken: string | null;
  /** Present when the reader arrived on the shared workspace join link. */
  joinToken: string | null;
  /** Present when the reader arrived on their own access link (D29). */
  acceptToken: string | null;
  /**
   * Who the link belongs to and what it will make them, read before it is spent
   * (D30). Null while the lookup is in flight, when there is no link, or when
   * the lookup could not be reached — in which case the accept screen falls
   * back to the plain "press the button" form rather than stranding anybody.
   */
  invitation: Invitation | null;
  invitationStatus: InvitationStatus;
  /** Why the link will not open, when it will not. */
  invitationError: string | null;
  /**
   * True when a link is being held by somebody signed in as a *different*
   * person — an administrator who opened a colleague's link, most likely.
   *
   * Worth its own flag because it changes two things: the dashboard must not
   * simply appear (they would never learn the link went unused), and an
   * administrator's link must not auto-enter (that would silently swap the
   * session they are already using for somebody else's).
   */
  invitationForSomebodyElse: boolean;
  /** Open an access link: no password, straight in. */
  acceptInvitation: () => Promise<void>;
  /** Put the link down unused, and stay as whoever is already signed in. */
  dismissInvitation: () => void;
  signIn: (email: string, password: string) => Promise<void>;
  register: (body: { email: string; password: string; displayName: string }) => Promise<void>;
  signOut: () => Promise<void>;
  /** Replace the cached user after a profile edit, without a round trip. */
  setUser: (user: User) => void;
  refresh: () => Promise<void>;
}

const EMPTY: SessionState = { user: null, bootstrap: false, invite_required: true };

export function useAuth(): Auth {
  const [state, setState] = useState<SessionState>(EMPTY);
  const [status, setStatus] = useState<AuthStatus>('loading');
  // Read once, at mount, before the tokens are stripped from the address bar.
  const [inviteToken, setInviteToken] = useState<string | null>(() =>
    inviteFromSearch(window.location.search),
  );
  const [joinToken, setJoinToken] = useState<string | null>(() =>
    joinFromSearch(window.location.search),
  );
  const [acceptToken, setAcceptToken] = useState<string | null>(() =>
    acceptFromSearch(window.location.search),
  );
  const [invitation, setInvitation] = useState<Invitation | null>(null);
  // Starts at `checking` when a token is present, so the very first render of a
  // page reached by a link already knows to hold the frame rather than paint a
  // sign-in form that is about to be replaced.
  const [invitationStatus, setInvitationStatus] = useState<InvitationStatus>(() =>
    acceptFromSearch(window.location.search) ? 'checking' : 'none',
  );
  const [invitationError, setInvitationError] = useState<string | null>(null);
  //: Which token has already been looked up. The effect below re-runs when the
  //: session resolves, and a lookup is cheap but not free — and running it twice
  //: would also mean two auto-enters racing for one link.
  const lookedUp = useRef<string | null>(null);
  //: Set once the accept below is in flight, so a re-render cannot start a
  //: second one. Two accepts of a link that has never been used are two INSERTs
  //: racing for one unique email — recoverable on the server, worth not causing.
  const spending = useRef(false);

  const refresh = useCallback(async () => {
    try {
      setState(await authApi.session());
      setStatus('ready');
    } catch (error) {
      // An API that cannot be reached is not "signed out". Saying so would put
      // a sign-in button on a page whose every other panel is already showing
      // its own connection error, and pressing it could only fail again.
      setStatus(error instanceof ApiError && error.status === 0 ? 'unreachable' : 'ready');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // A session can die while the tab is open — it expires, an administrator
  // deactivates the account, or the password is changed elsewhere. The next call
  // to any endpoint answers 401, and this turns that into the sign-in page
  // instead of a dashboard full of individual error panels.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setState((prev) => (prev.user ? { ...prev, user: null } : prev));
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  // Take the tokens out of the URL as soon as they have been read into state.
  useEffect(() => {
    if (!inviteToken && !joinToken && !acceptToken) return;
    const next = withoutInvite(window.location.search);
    window.history.replaceState(
      null,
      '',
      next ? `${window.location.pathname}?${next}` : window.location.pathname,
    );
  }, [inviteToken, joinToken, acceptToken]);

  const signIn = useCallback(async (email: string, password: string) => {
    const user = await authApi.login(email, password);
    setState((prev) => ({ ...prev, user, bootstrap: false, invite_required: true }));
    setStatus('ready');
  }, []);

  const register = useCallback(
    async (body: { email: string; password: string; displayName: string }) => {
      const user = await authApi.register({
        email: body.email,
        password: body.password,
        display_name: body.displayName,
        invite_token: inviteToken,
        join_token: joinToken,
      });
      // The invitation is spent, so it must not be offered to the next form.
      // The join link is not spent — it is reusable by design — but this reader
      // has used it, and holding it would offer them a form they cannot use.
      setInviteToken(null);
      setJoinToken(null);
      setState({ user, bootstrap: false, invite_required: true });
      setStatus('ready');
    },
    [inviteToken, joinToken],
  );

  const acceptInvitation = useCallback(async () => {
    if (!acceptToken) return;
    const user = await authApi.accept(acceptToken);
    // The token stays valid — it is durable by design — but this reader has
    // used it, and holding it would keep offering them a button they no longer
    // need.
    setAcceptToken(null);
    setInvitation(null);
    setInvitationStatus('none');
    setInvitationError(null);
    setState({ user, bootstrap: false, invite_required: true });
    setStatus('ready');
  }, [acceptToken]);

  /**
   * Read the link. Fired at mount, alongside the session call, not after it.
   *
   * The two answers are independent — who is signed in here, and whose link this
   * is — and asking for them in sequence would put the invited person behind two
   * round trips of blank frame on the one journey this whole feature is about.
   * The *decision* needs both, and that is the effect below.
   */
  useEffect(() => {
    if (!acceptToken || lookedUp.current === acceptToken) return;
    lookedUp.current = acceptToken;

    let cancelled = false;
    void (async () => {
      try {
        const found = await authApi.invitation(acceptToken);
        if (!cancelled) setInvitation(found);
      } catch (caught) {
        if (cancelled) return;
        if (caught instanceof ApiError && caught.status !== 0) {
          // The API says this link is not valid. Saying so now is better than
          // offering a button whose only possible outcome is the same message.
          setInvitationError(caught.message);
          setInvitationStatus('dead');
        } else {
          // The API could not be *asked* — which is not the same as a refusal.
          // Fall back to the plain accept screen: one button, no claims about
          // who is holding the link, and pressing it is still what decides.
          setInvitationStatus('ready');
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [acceptToken]);

  /**
   * Both answers are in: enter, or offer the accept screen (D30).
   *
   * Entering is for a browser with **no session**, which is what somebody
   * opening their invitation has. A browser already signed in needs nothing
   * spent on its behalf: if the link is its own owner's they are already where
   * it would take them, and if it is somebody else's, silently swapping one live
   * session for another is the last thing to do without asking. Both fall
   * through to `ready`, and App chooses between the dashboard and the accept
   * screen from `invitationForSomebodyElse`.
   *
   * No cancellation on this one, deliberately: it sets `entering` itself, which
   * re-runs the effect, and a cleanup that flipped a `cancelled` flag would
   * throw away the result of the very request it had just started.
   */
  useEffect(() => {
    if (invitationStatus !== 'checking' || status !== 'ready') return;
    if (!acceptToken || !invitation) return;

    if (!landsStraightInDashboard(invitation.role) || state.user !== null) {
      setInvitationStatus('ready');
      return;
    }
    if (spending.current) return;
    spending.current = true;
    setInvitationStatus('entering');

    void (async () => {
      let user: User;
      try {
        user = await authApi.accept(acceptToken);
      } catch (caught) {
        // Never strand an administrator on a screen with nothing on it. The
        // accept screen has a button, so a transient failure becomes a retry.
        setInvitationError(
          caught instanceof ApiError ? caught.message : 'Something went wrong. Try again.',
        );
        setInvitationStatus('ready');
        return;
      }
      // Straight from the response rather than re-reading the session: one round
      // trip, and no frame in between where the page has accepted but does not
      // yet know who it accepted.
      setAcceptToken(null);
      setInvitation(null);
      setInvitationStatus('none');
      setState({ user, bootstrap: false, invite_required: true });
    })();
  }, [invitationStatus, status, acceptToken, invitation, state.user]);

  /**
   * Put a link down without using it.
   *
   * Only ever offered to somebody signed in as a *different* person — an
   * administrator who opened a colleague's link to check it. Without this the
   * screen's only action is to sign them out of their own account and into the
   * colleague's, and the only way back is a reload they have to think of. A page
   * whose sole button does the thing you do not want is a trap, however clearly
   * it is labelled.
   */
  const dismissInvitation = useCallback(() => {
    setAcceptToken(null);
    setInvitation(null);
    setInvitationStatus('none');
    setInvitationError(null);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      // Cleared even if the call failed. The alternative leaves someone looking
      // at their own name after pressing "Sign out", which is worse than a
      // client that is briefly ahead of the server — and the next reply from
      // /api/auth/session corrects it either way.
      setState((prev) => ({ ...prev, user: null }));
    }
  }, []);

  const setUser = useCallback((user: User) => {
    setState((prev) => ({ ...prev, user }));
  }, []);

  return {
    status,
    user: state.user,
    bootstrap: state.bootstrap,
    inviteRequired: state.invite_required,
    inviteToken,
    joinToken,
    acceptToken,
    invitation,
    invitationStatus,
    invitationError,
    invitationForSomebodyElse:
      invitation !== null && state.user !== null && invitation.email !== state.user.email,
    acceptInvitation,
    dismissInvitation,
    signIn,
    register,
    signOut,
    setUser,
    refresh,
  };
}
