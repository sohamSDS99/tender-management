import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * The administrator's half of D30: addresses and roles first, links second.
 *
 * This panel had no test of any kind, which is why it gets one now — the rule it
 * carries is a *sequence*, and a sequence enforced only by a disabled attribute
 * is one careless edit from not being enforced at all. The API refuses a roster
 * POST with no role, so the worst case was never a link with a random role; it
 * was a form that looked fine and answered 422 on submit.
 *
 * What is real here: the component, its state, and every branch of its copy.
 * Only the API client is stubbed, because the question is what the panel does
 * with an answer, not how it asks.
 */

const rosterMock = vi.fn();
const addMock = vi.fn();
const setRoleMock = vi.fn();
const issueLinkMock = vi.fn();

vi.mock('../../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  auth: {
    roster: (...args: unknown[]) => rosterMock(...args),
    addToRoster: (...args: unknown[]) => addMock(...args),
    setRosterRole: (...args: unknown[]) => setRoleMock(...args),
    issueAccessLink: (...args: unknown[]) => issueLinkMock(...args),
    revokeAccessLink: vi.fn(),
    removeFromRoster: vi.fn(),
  },
}));

let container: HTMLDivElement;
let root: Root;

function entry(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    email: 'colleague@sdsmanager.com',
    role: 'member',
    note: '',
    created_at: '2026-08-27T00:00:00Z',
    joined_at: null,
    access_url: 'http://tenders.local/?accept=a-live-token',
    ...over,
  };
}

function view(entries: Record<string, unknown>[]) {
  const joined = entries.filter((e) => e.joined_at).length;
  return { entries, total: entries.length, joined, waiting: entries.length - joined };
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  rosterMock.mockReset();
  addMock.mockReset();
  setRoleMock.mockReset();
  issueLinkMock.mockReset();
  rosterMock.mockResolvedValue(view([]));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render() {
  const { WorkspaceRoster } = await import('./WorkspaceRoster');
  await act(async () => {
    root.render(<WorkspaceRoster />);
  });
}

const buttons = () => [...container.querySelectorAll('button')];
const button = (text: string) => buttons().find((b) => b.textContent?.includes(text))!;
const click = async (el: Element) => {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
};

/**
 * Type into a controlled field.
 *
 * Assigning `.value` directly is not enough: React keeps its own value tracker
 * on the node, sees no change, and skips the onChange — so the test would drive
 * a component that never heard the keystroke. Going through the prototype's
 * setter is what a real key press does.
 */
const type = async (el: HTMLTextAreaElement | HTMLInputElement, value: string) => {
  const proto =
    el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
  await act(async () => {
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
};

describe('the role has to be chosen before any link is generated', () => {
  it('will not submit with addresses alone', async () => {
    await render();

    await type(container.querySelector('textarea')!, 'newstarter@sdsmanager.com');

    expect(button('Add and generate links').hasAttribute('disabled')).toBe(true);
    expect(container.textContent).toContain('Choose Member or Administrator');
  });

  it('starts with neither role pressed, so nothing is chosen by default', async () => {
    await render();
    const seg = [...container.querySelectorAll('[aria-label="Role"] button')];
    expect(seg).toHaveLength(2);
    expect(seg.every((b) => b.getAttribute('aria-pressed') === 'false')).toBe(true);
  });

  it('sends the chosen role once both halves are given', async () => {
    addMock.mockResolvedValue({ added: [entry()], already_present: [] });
    await render();

    await type(container.querySelector('textarea')!, 'deputy@sdsmanager.com');
    await click(button('Administrator'));
    expect(button('Add and generate links').hasAttribute('disabled')).toBe(false);
    await click(button('Add and generate links'));

    expect(addMock).toHaveBeenCalledWith({
      addresses: 'deputy@sdsmanager.com',
      role: 'admin',
      note: '',
    });
    expect(container.textContent).toContain('Send each person theirs');
  });
});

describe('every row says where its link lands', () => {
  it('spells out that an administrator goes straight in', async () => {
    rosterMock.mockResolvedValue(view([entry({ role: 'admin' })]));
    await render();
    expect(container.textContent).toContain('Opens the dashboard directly');
  });

  it('spells out that a member is asked first', async () => {
    rosterMock.mockResolvedValue(view([entry({ role: 'member' })]));
    await render();
    expect(container.textContent).toContain('Shows the accept screen');
  });

  it('claims neither for somebody who has already joined', async () => {
    // Their landing follows the *account's* role by then, which this row does
    // not know — an administrator may have promoted them under People.
    rosterMock.mockResolvedValue(
      view([entry({ role: 'member', joined_at: '2026-08-27T09:00:00Z' })]),
    );
    await render();

    expect(container.textContent).toContain('Signs them in again');
    expect(container.textContent).not.toContain('Shows the accept screen');
    expect(container.textContent).not.toContain('Opens the dashboard directly');
  });
});

describe('changing a role explains what it did to the link', () => {
  it('says the link was withdrawn, and offers a new one', async () => {
    rosterMock.mockResolvedValueOnce(view([entry({ role: 'member' })]));
    setRoleMock.mockResolvedValue(entry({ role: 'admin', access_url: null }));
    // What the server sends back on the reload: re-roling revoked the link.
    rosterMock.mockResolvedValue(view([entry({ role: 'admin', access_url: null })]));
    await render();

    const admin = [...container.querySelectorAll('[aria-label^="Role for"] button')].find((b) =>
      b.textContent?.includes('Admin'),
    )!;
    await click(admin);

    expect(setRoleMock).toHaveBeenCalledWith(1, 'admin');
    expect(container.textContent).toContain('the old link was withdrawn');
    expect(button('Generate link')).toBeDefined();
  });

  it('does not blame a role change for a link that was never there', async () => {
    rosterMock.mockResolvedValue(view([entry({ role: 'member', access_url: null })]));
    setRoleMock.mockResolvedValue(entry({ role: 'admin', access_url: null }));
    await render();

    const admin = [...container.querySelectorAll('[aria-label^="Role for"] button')].find((b) =>
      b.textContent?.includes('Admin'),
    )!;
    await click(admin);

    expect(container.textContent).toContain('No link — revoked, or never issued');
    expect(container.textContent).not.toContain('the old link was withdrawn');
  });

  it('leaves the control alone once they have joined', async () => {
    rosterMock.mockResolvedValue(
      view([entry({ role: 'member', joined_at: '2026-08-27T09:00:00Z' })]),
    );
    await render();

    const seg = [...container.querySelectorAll('[aria-label^="Role for"] button')];
    expect(seg.every((b) => b.hasAttribute('disabled'))).toBe(true);
    expect(seg[0].getAttribute('title')).toContain('Change their role under People');
  });
});
