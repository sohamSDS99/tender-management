import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Auth } from '../../state/auth';
import type { User } from '../../types';

/**
 * Signing out must not be a lockout (D31).
 *
 * The defect was reported from use, and the page is where it was visible: a
 * colleague who joined by access link pressed **Sign out**, was shown a form
 * asking for a password they had never had, and there was nothing on any screen
 * telling them — or the administrator — that this was going to happen.
 *
 * So these tests are about what the page *says* and *offers*, which is the half
 * the API cannot prove: that a passwordless account is warned before it strands
 * itself, that it is given a form with no impossible field in it, and that an
 * administrator can see who is in that state and fix it.
 */

const sessionsMock = vi.fn();
const changePasswordMock = vi.fn();
const usersMock = vi.fn();
const invitesMock = vi.fn();
const rosterMock = vi.fn();
const createUserMock = vi.fn();
const setUserPasswordMock = vi.fn();

vi.mock('../../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  auth: {
    sessions: (...a: unknown[]) => sessionsMock(...a),
    changePassword: (...a: unknown[]) => changePasswordMock(...a),
    users: (...a: unknown[]) => usersMock(...a),
    invites: (...a: unknown[]) => invitesMock(...a),
    roster: (...a: unknown[]) => rosterMock(...a),
    createUser: (...a: unknown[]) => createUserMock(...a),
    setUserPassword: (...a: unknown[]) => setUserPasswordMock(...a),
    updateProfile: vi.fn(),
    signOutOthers: vi.fn(),
    updateUser: vi.fn(),
    createInvite: vi.fn(),
    revokeInvite: vi.fn(),
    addToRoster: vi.fn(),
    setRosterRole: vi.fn(),
    removeFromRoster: vi.fn(),
    issueAccessLink: vi.fn(),
    revokeAccessLink: vi.fn(),
  },
}));

let container: HTMLDivElement;
let root: Root;

function user(over: Partial<User> = {}): User {
  return {
    id: 9,
    email: 'colleague@sdsmanager.com',
    display_name: 'Colleague',
    role: 'member',
    is_active: true,
    has_password: false,
    created_at: '2026-08-27T00:00:00Z',
    last_login_at: '2026-08-27T09:00:00Z',
    ...over,
  };
}

function authFor(current: User): Auth {
  return {
    status: 'ready',
    user: current,
    bootstrap: false,
    inviteRequired: true,
    inviteToken: null,
    joinToken: null,
    acceptToken: null,
    invitation: null,
    invitationStatus: 'none',
    invitationError: null,
    invitationForSomebodyElse: false,
    acceptInvitation: vi.fn(),
    dismissInvitation: vi.fn(),
    signIn: vi.fn(),
    register: vi.fn(),
    signOut: vi.fn(),
    setUser: vi.fn(),
    refresh: vi.fn(),
  };
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  for (const m of [
    sessionsMock,
    changePasswordMock,
    usersMock,
    invitesMock,
    rosterMock,
    createUserMock,
    setUserPasswordMock,
  ]) {
    m.mockReset();
  }
  sessionsMock.mockResolvedValue([]);
  usersMock.mockResolvedValue([]);
  invitesMock.mockResolvedValue([]);
  rosterMock.mockResolvedValue({ entries: [], total: 0, joined: 0, waiting: 0 });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(current: User) {
  const { AccountSettings } = await import('./AccountSettings');
  await act(async () => {
    root.render(<AccountSettings auth={authFor(current)} onBack={() => {}} />);
  });
}

const buttons = () => [...container.querySelectorAll('button')];
const button = (text: string) => buttons().find((b) => b.textContent?.includes(text));
const click = async (el: Element) => {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
};
const type = async (el: HTMLInputElement, value: string) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
  await act(async () => {
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
};
const submit = async (form: HTMLFormElement) => {
  await act(async () => {
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
};

// --- the account that can lock itself out -----------------------------------

describe('an account with no password', () => {
  it('is warned that signing out would strand it', async () => {
    await render(user({ has_password: false }));

    expect(container.textContent).toContain('Signing out now would lock you out');
    expect(container.textContent).toContain('Set a password');
  });

  it('is not asked for a current password it has never had', async () => {
    await render(user({ has_password: false }));

    expect(container.textContent).not.toContain('Current password');
    expect(container.querySelector('input[autocomplete="current-password"]')).toBeNull();
  });

  it('sends null rather than an empty string for the password it does not have', async () => {
    // An empty string is a *claim* about the current password, and the server
    // refuses one for an account that has none. Null says "there isn't one".
    changePasswordMock.mockResolvedValue({ revoked: 0 });
    await render(user({ has_password: false }));

    const field = container.querySelector('input[autocomplete="new-password"]') as HTMLInputElement;
    await type(field, 'a-brand-new-long-password');
    await submit(field.closest('form')!);

    expect(changePasswordMock).toHaveBeenCalledWith(null, 'a-brand-new-long-password');
    expect(container.textContent).toContain('Password set');
  });
});

describe('an account that already has a password', () => {
  it('still has to prove the old one', async () => {
    await render(user({ has_password: true }));

    expect(container.textContent).toContain('Current password');
    expect(container.querySelector('input[autocomplete="current-password"]')).not.toBeNull();
    expect(container.textContent).not.toContain('Signing out now would lock you out');
  });

  it('sends the current password it was given', async () => {
    changePasswordMock.mockResolvedValue({ revoked: 2 });
    await render(user({ has_password: true }));

    await type(
      container.querySelector('input[autocomplete="current-password"]') as HTMLInputElement,
      'the-old-one',
    );
    const next = container.querySelector('input[autocomplete="new-password"]') as HTMLInputElement;
    await type(next, 'the-new-long-one');
    await submit(next.closest('form')!);

    expect(changePasswordMock).toHaveBeenCalledWith('the-old-one', 'the-new-long-one');
    expect(container.textContent).toContain('Password changed');
    expect(container.textContent).toContain('2 other sessions were signed out');
  });
});

// --- what a member is told, and what an administrator can do ----------------

describe('the administrator’s view of who can get back in', () => {
  const BOSS = user({ id: 1, email: 'boss@sdsmanager.com', role: 'admin', has_password: true });

  it('marks the people who have no password', async () => {
    usersMock.mockResolvedValue([BOSS, user({ has_password: false })]);
    await render(BOSS);

    expect(container.textContent).toContain('No password');
    expect(button('Set password')).toBeDefined();
    expect(button('Reset password')).toBeDefined();
  });

  it('sets a password for somebody, and says what it did to their sessions', async () => {
    usersMock.mockResolvedValue([BOSS, user({ id: 9, has_password: false })]);
    setUserPasswordMock.mockResolvedValue({ revoked: 1 });
    await render(BOSS);

    await click(button('Set password')!);
    const field = container.querySelector(
      'input[aria-label="New password for colleague@sdsmanager.com"]',
    ) as HTMLInputElement;
    await type(field, 'a-password-for-them');
    await submit(field.closest('form')!);

    expect(setUserPasswordMock).toHaveBeenCalledWith(9, 'a-password-for-them');
    expect(container.textContent).toContain('can now sign in with that password');
    // The administrator has to know they just signed somebody out, or they will
    // hear about it from that person instead.
    expect(container.textContent).toContain('1 session was signed out');
  });

  it('creates an account with a password, and will not do it without a role', async () => {
    usersMock.mockResolvedValue([BOSS]);
    createUserMock.mockResolvedValue(user({ email: 'yasha@sdsmanager.com', has_password: true }));
    await render(BOSS);

    // Scoped to the add-person form, not the page: the profile section above it
    // has its own email field and the password section its own new-password one,
    // so an unscoped selector reaches the wrong form.
    const form = container
      .querySelector('[aria-label="Role for the new account"]')!
      .closest('form')! as HTMLFormElement;
    await type(
      form.querySelector('input[type="email"]') as HTMLInputElement,
      'yasha@sdsmanager.com',
    );
    await type(
      form.querySelector('input[autocomplete="new-password"]') as HTMLInputElement,
      'a-long-enough-password',
    );

    const create = button('Create the account')!;
    expect(create.hasAttribute('disabled')).toBe(true);
    expect(container.textContent).toContain('Choose Member or Administrator');

    const memberButton = [
      ...form.querySelectorAll('[aria-label="Role for the new account"] button'),
    ].find((b) => b.textContent === 'Member')!;
    await click(memberButton);
    expect(create.hasAttribute('disabled')).toBe(false);
    await submit(form);

    expect(createUserMock).toHaveBeenCalledWith({
      email: 'yasha@sdsmanager.com',
      display_name: '',
      role: 'member',
      password: 'a-long-enough-password',
    });
    expect(container.textContent).toContain('nothing was emailed');
  });

  it('shows a member none of it, and says whose job it is', async () => {
    await render(user({ role: 'member', has_password: true }));

    expect(button('Create the account')).toBeUndefined();
    expect(button('Set password')).toBeUndefined();
    expect(container.textContent).toContain('Only an administrator can add people');
  });
});
