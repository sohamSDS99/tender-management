import { useCallback, useEffect, useState } from 'react';
import { ApiError, auth as authApi, setUnauthorizedHandler } from '../api/client';
import type { SessionState, User } from '../types';

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

export type AuthStatus = 'loading' | 'ready' | 'unreachable';

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
    if (!inviteToken && !joinToken) return;
    const next = withoutInvite(window.location.search);
    window.history.replaceState(
      null,
      '',
      next ? `${window.location.pathname}?${next}` : window.location.pathname,
    );
  }, [inviteToken, joinToken]);

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
    signIn,
    register,
    signOut,
    setUser,
    refresh,
  };
}
