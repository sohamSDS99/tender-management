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

vi.mock('./api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  auth: { session: (...args: unknown[]) => sessionMock(...args) },
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
