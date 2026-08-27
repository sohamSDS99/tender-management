import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * The client half of the sign-in gate (D26).
 *
 * This file exists because the gate shipped with none of it. Every other test in
 * this project is a pure function, `vitest.config.ts` only globbed `*.test.ts`,
 * and so the one component whose whole job is deciding what a signed-out person
 * may see had no coverage at all — a refactor that rendered the Dashboard
 * unconditionally would have gone through green.
 *
 * **What this does and does not prove.** The real control is on the server:
 * `enforce_sign_in` refuses every route, so even a Dashboard rendered by mistake
 * would show errors rather than tenders. What these tests protect is the
 * property the server cannot give you — that the dashboard is never *mounted*,
 * and therefore never fires the dozen requests it makes on mount, never fills
 * the console with 401s, and never paints headings and counts at somebody who is
 * not entitled to them.
 *
 * The Dashboard is stubbed on purpose. Mounting the real one would drag in the
 * whole data layer and test the wrong thing; the question here is only which
 * branch App takes.
 */

const sessionMock = vi.fn();
const acceptMock = vi.fn();
const invitationMock = vi.fn();

vi.mock('./api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  auth: {
    session: (...args: unknown[]) => sessionMock(...args),
    accept: (...args: unknown[]) => acceptMock(...args),
    invitation: (...args: unknown[]) => invitationMock(...args),
  },
  setUnauthorizedHandler: vi.fn(),
}));

vi.mock('./pages/Dashboard', () => ({
  Dashboard: () => <div data-testid="dashboard">the inside pages</div>,
}));

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  sessionMock.mockReset();
  acceptMock.mockReset();
  invitationMock.mockReset();
  // Each test decides its own URL. jsdom starts at "/", and the accept token is
  // read from the query string at mount.
  window.history.replaceState(null, '', '/');
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render() {
  const { default: App } = await import('./App');
  await act(async () => {
    root.render(<App />);
  });
}

describe('the sign-in gate', () => {
  it('does not mount the dashboard for a signed-out visitor', async () => {
    sessionMock.mockResolvedValue({ user: null, bootstrap: false, invite_required: true });
    await render();

    expect(container.querySelector('[data-testid="dashboard"]')).toBeNull();
    expect(container.textContent).toContain('Sign in');
  });

  it('says the dashboard is not public rather than just asking for a password', async () => {
    // Someone who lands here uninvited should learn why, not conclude the site
    // is broken.
    sessionMock.mockResolvedValue({ user: null, bootstrap: false, invite_required: true });
    await render();
    expect(container.textContent).toContain('Nothing here is public');
  });

  it('mounts the dashboard once there is a session', async () => {
    sessionMock.mockResolvedValue({
      user: {
        id: 1,
        email: 'a@b.com',
        display_name: 'A',
        role: 'admin',
        is_active: true,
        created_at: '2026-01-01T00:00:00Z',
        last_login_at: null,
      },
      bootstrap: false,
      invite_required: true,
    });
    await render();

    expect(container.querySelector('[data-testid="dashboard"]')).not.toBeNull();
    // ...and the door is gone once you are through it.
    expect(container.textContent).not.toContain('Nothing here is public');
  });

  it('offers the first account rather than a password prompt on an empty deployment', async () => {
    sessionMock.mockResolvedValue({ user: null, bootstrap: true, invite_required: false });
    await render();

    expect(container.textContent).toContain('First account');
    expect(container.textContent).toContain('becomes the administrator');
    expect(container.querySelector('[data-testid="dashboard"]')).toBeNull();
  });

  it('shows neither the dashboard nor a sign-in form while the session is unknown', async () => {
    // The window between page load and the first reply. A sign-in form flashed
    // here reads as "you have been logged out" to somebody who has not been.
    sessionMock.mockReturnValue(new Promise(() => {}));
    await render();

    expect(container.querySelector('[data-testid="dashboard"]')).toBeNull();
    expect(container.textContent).not.toContain('Sign in');
  });

  it('does not offer a sign-in form when the API cannot be reached', async () => {
    // Offering a button whose only possible outcome is another failure.
    const { ApiError } = await import('./api/client');
    sessionMock.mockRejectedValue(new ApiError('Cannot reach the API', 0));
    await render();

    expect(container.textContent).toContain('Cannot reach the API');
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(container.querySelector('[data-testid="dashboard"]')).toBeNull();
  });
});

const ACCOUNT = {
  id: 7,
  email: 'tanjir@sdsmanager.com',
  display_name: 'tanjir',
  role: 'admin',
  is_active: true,
  created_at: '2026-08-27T00:00:00Z',
  last_login_at: '2026-08-27T00:00:00Z',
};

/**
 * The two journeys a link can take, which is the whole of D30.
 *
 * A **member** opens their link, is told who they are and what they are joining,
 * and presses one button. An **administrator** opens theirs and is simply in.
 * Covered here rather than only at the API level because the API cannot show
 * which page was drawn, and "which page was drawn" is the entire feature — the
 * same endpoint serves both.
 *
 * The unfurl rule from D29 still holds and is still tested: a member's link is
 * never spent without a press. What changed is that an administrator's is, by a
 * real browser executing the app, which no link preview does.
 */
describe('arriving on an access link', () => {
  const MEMBER = {
    ...ACCOUNT,
    id: 9,
    email: 'colleague@sdsmanager.com',
    display_name: 'colleague',
    role: 'member',
  };

  function arriveOnALink(
    token = 'a-real-looking-token',
    invitation: Record<string, unknown> | null = {
      email: 'colleague@sdsmanager.com',
      role: 'member',
      joined: false,
    },
  ) {
    window.history.replaceState(null, '', `/?accept=${token}`);
    sessionMock.mockResolvedValue({ user: null, bootstrap: false, invite_required: true });
    if (invitation) invitationMock.mockResolvedValue(invitation);
  }

  /** One more turn of the loop, for a state change behind two awaits. */
  async function settle() {
    await act(async () => {});
  }

  // --- a member: one button, and nothing happens until it is pressed ---------

  it('offers a member a button and asks for nothing at all', async () => {
    arriveOnALink();
    await render();
    await settle();

    expect(container.textContent).toContain('Accept invitation');
    expect(container.textContent).toContain('There is no password to set');
    // The whole point: no fields, of any kind.
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(container.querySelector('input[type="email"]')).toBeNull();
    expect(container.querySelector('input')).toBeNull();
    expect(container.querySelector('[data-testid="dashboard"]')).toBeNull();
  });

  it('names the address the link belongs to, and the role it grants', async () => {
    // Two colleagues who forwarded each other the wrong link would otherwise
    // both join as the wrong person and nobody would find out.
    arriveOnALink();
    await render();
    await settle();

    expect(container.textContent).toContain('colleague@sdsmanager.com');
    expect(container.textContent).toContain('Link belongs to');
    expect(container.textContent).toContain('Member');
  });

  it('lands a member on the dashboard when the button is pressed', async () => {
    arriveOnALink('the-token-from-the-link');
    acceptMock.mockResolvedValue(MEMBER);
    await render();
    await settle();

    const button = [...container.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('Accept invitation'),
    )!;
    await act(async () => {
      button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(acceptMock).toHaveBeenCalledWith('the-token-from-the-link');
    expect(container.querySelector('[data-testid="dashboard"]')).not.toBeNull();
    expect(container.textContent).not.toContain('Accept invitation');
  });

  it("does not spend a member's link on its own, before the button is pressed", async () => {
    // A chat client fetching the URL for a preview must not consume the
    // invitation. The page runs no JS for an unfurl, and even in a real browser
    // nothing happens here until somebody clicks.
    arriveOnALink();
    await render();
    await settle();
    expect(acceptMock).not.toHaveBeenCalled();
  });

  it('greets somebody who already has an account instead of offering to create one', async () => {
    arriveOnALink('durable-token', {
      email: 'colleague@sdsmanager.com',
      role: 'member',
      joined: true,
    });
    await render();
    await settle();

    expect(container.textContent).toContain('Welcome back');
    expect(container.textContent).toContain('Continue to the dashboard');
    expect(container.textContent).not.toContain('There is no password to set');
    expect(acceptMock).not.toHaveBeenCalled();
  });

  // --- an administrator: no button at all -----------------------------------

  it("spends an administrator's link on arrival and shows the dashboard", async () => {
    arriveOnALink('an-admin-token', {
      email: 'tanjir@sdsmanager.com',
      role: 'admin',
      joined: false,
    });
    acceptMock.mockResolvedValue(ACCOUNT);
    await render();
    await settle();
    await settle();

    expect(invitationMock).toHaveBeenCalledWith('an-admin-token');
    expect(acceptMock).toHaveBeenCalledWith('an-admin-token');
    expect(container.querySelector('[data-testid="dashboard"]')).not.toBeNull();
    expect(container.textContent).not.toContain('Accept invitation');
  });

  it('never shows an administrator a sign-in form on the way through', async () => {
    // The failure this guards against is a frame of the wrong page: the session
    // resolves as signed-out before the link is read, and a gate that rendered
    // on that would flash a password box at somebody being signed in.
    arriveOnALink('an-admin-token', {
      email: 'tanjir@sdsmanager.com',
      role: 'admin',
      joined: false,
    });
    acceptMock.mockReturnValue(new Promise(() => {}));
    await render();
    await settle();

    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(container.textContent).toContain('Opening the dashboard');
    expect(container.textContent).not.toContain('Accept invitation');
  });

  it('falls back to the button when accepting fails for an administrator', async () => {
    // Never strand somebody on a screen with nothing on it. A transient failure
    // has to become something they can press again.
    const { ApiError } = await import('./api/client');
    arriveOnALink('an-admin-token', {
      email: 'tanjir@sdsmanager.com',
      role: 'admin',
      joined: false,
    });
    acceptMock.mockRejectedValue(new ApiError('The API fell over.', 500));
    await render();
    await settle();
    await settle();

    expect(container.textContent).toContain('The API fell over.');
    expect(
      [...container.querySelectorAll('button')].some((b) =>
        b.textContent?.includes('Accept invitation'),
      ),
    ).toBe(true);
  });

  // --- somebody else is already signed in on this browser -------------------

  it("will not swap a live session for somebody else's link without asking", async () => {
    // An administrator testing a colleague's link. Auto-entering here would
    // silently sign them out of their own account and into that colleague's.
    window.history.replaceState(null, '', '/?accept=a-deputy-token');
    sessionMock.mockResolvedValue({ user: ACCOUNT, bootstrap: false, invite_required: true });
    invitationMock.mockResolvedValue({
      email: 'deputy@sdsmanager.com',
      role: 'admin',
      joined: false,
    });
    await render();
    await settle();

    expect(acceptMock).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="dashboard"]')).toBeNull();
    expect(container.textContent).toContain('deputy@sdsmanager.com');
    expect(container.textContent).toContain('tanjir@sdsmanager.com');
  });

  it('offers a way to put somebody else’s link down and stay as yourself', async () => {
    // Without this the only button on the page signs them out of their own
    // account and into the colleague's. A page whose sole action is the one you
    // do not want is a trap however clearly it is labelled.
    window.history.replaceState(null, '', '/?accept=a-deputy-token');
    sessionMock.mockResolvedValue({ user: ACCOUNT, bootstrap: false, invite_required: true });
    invitationMock.mockResolvedValue({
      email: 'deputy@sdsmanager.com',
      role: 'admin',
      joined: false,
    });
    await render();
    await settle();

    const out = [...container.querySelectorAll('button')].find((b) =>
      b.textContent?.includes('stay signed in as'),
    )!;
    expect(out).toBeDefined();
    await act(async () => {
      out.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(acceptMock).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="dashboard"]')).not.toBeNull();
  });

  it('reads the link without waiting for the session to come back', async () => {
    // The two answers are independent, and asking for them in sequence puts the
    // invited person behind two round trips of blank screen on the one journey
    // this feature is about. Proven by never answering the session call at all.
    window.history.replaceState(null, '', '/?accept=a-token');
    sessionMock.mockReturnValue(new Promise(() => {}));
    invitationMock.mockResolvedValue({
      email: 'colleague@sdsmanager.com',
      role: 'member',
      joined: false,
    });
    await render();
    await settle();

    expect(invitationMock).toHaveBeenCalledWith('a-token');
    // ...and still decides nothing until the session lands.
    expect(container.textContent).not.toContain('Accept invitation');
    expect(acceptMock).not.toHaveBeenCalled();
  });

  it('goes quietly to the dashboard when the link is the signed-in person’s own', async () => {
    window.history.replaceState(null, '', '/?accept=my-own-token');
    sessionMock.mockResolvedValue({ user: ACCOUNT, bootstrap: false, invite_required: true });
    invitationMock.mockResolvedValue({
      email: 'tanjir@sdsmanager.com',
      role: 'admin',
      joined: true,
    });
    await render();
    await settle();

    expect(acceptMock).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="dashboard"]')).not.toBeNull();
  });

  // --- links that will not open ---------------------------------------------

  it('says a dead link is dead before anybody presses anything', async () => {
    const { ApiError } = await import('./api/client');
    window.history.replaceState(null, '', '/?accept=revoked-token');
    sessionMock.mockResolvedValue({ user: null, bootstrap: false, invite_required: true });
    invitationMock.mockRejectedValue(new ApiError('That link is not valid.', 400));
    await render();
    await settle();

    expect(container.textContent).toContain('That link is not valid.');
    expect(container.textContent).toContain('That link has expired');
    expect(acceptMock).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="dashboard"]')).toBeNull();
  });

  it('still offers the button when the lookup itself could not be reached', async () => {
    // A refusal and an unreachable API are different answers. Treating the
    // second as the first would turn a flaky network into "your link is dead".
    const { ApiError } = await import('./api/client');
    window.history.replaceState(null, '', '/?accept=fine-token');
    sessionMock.mockResolvedValue({ user: null, bootstrap: false, invite_required: true });
    invitationMock.mockRejectedValue(new ApiError('Cannot reach the API', 0));
    await render();
    await settle();

    expect(container.textContent).toContain('Accept invitation');
    expect(container.textContent).not.toContain('Link belongs to');
  });

  it('takes the token out of the address bar on arrival', async () => {
    // It is a live credential. Left in the URL it ends up in browser history and
    // in any screenshot of the page.
    arriveOnALink('live-credential');
    await render();
    await settle();
    expect(window.location.search).not.toContain('live-credential');
  });

  it('shows the ordinary sign-in page when there is no token', async () => {
    sessionMock.mockResolvedValue({ user: null, bootstrap: false, invite_required: true });
    await render();
    expect(container.textContent).not.toContain('Accept invitation');
    expect(container.textContent).toContain('Sign in');
    expect(invitationMock).not.toHaveBeenCalled();
  });
});
