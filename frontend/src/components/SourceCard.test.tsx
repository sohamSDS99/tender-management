import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SourceStatus } from '../types';

/**
 * Tender Impulse issues three things - an access token, an encryption key and
 * a starting id - and all three are set on its own card (D39, D41).
 *
 * The encryption key is the first second-half that is itself a secret. The
 * email and saved-search id before it are read back in full on purpose, so
 * the card has to be told which kind it is holding.
 */

const setSettingsSecret = vi.fn().mockResolvedValue(undefined);

vi.mock('../api/client', () => ({
  api: {
    setCredential: vi.fn(),
    setSettingsSecret: (...args: unknown[]) => setSettingsSecret(...args),
  },
}));

let container: HTMLDivElement;
let root: Root;

function tenderImpulse(over: Partial<SourceStatus> = {}): SourceStatus {
  return {
    name: 'tender_impulse',
    display_name: 'Tender Impulse (global)',
    homepage: 'https://tenderimpulse.com',
    enabled: true,
    requires_api_key: true,
    credential_label: 'access token',
    credential_extra_field: 'tender_impulse_encryption_key',
    credential_extra_label: 'Encryption key',
    credential_extra_hint: 'Issued with the access token.',
    credential_extra_placeholder: 'Paste the encryption key',
    credential_extra_configured: true,
    credential_extra_value: '…1234',
    credential_extra_secret: true,
    setup_fields: [
      {
        field: 'tender_impulse_start_id',
        label: 'Starting id',
        hint: 'The lastid Tender Impulse gives you to start from.',
        placeholder: '8156393',
        configured: true,
        value: '8156393',
      },
    ],
    credential_configured: true,
    credential_hint: '…oken',
    unavailable_reason: null,
    keyword_prefiltered: true,
    notes: 'A paid global aggregator.',
    tender_count: 0,
    running: false,
    last_status: null,
    last_run_at: null,
    last_success_at: null,
    last_error: null,
    ...over,
  };
}

async function render(source: SourceStatus) {
  const { SourceCard } = await import('./SourceCard');
  await act(async () => {
    root.render(<SourceCard source={source} busySource={null} onFetch={() => {}} detailed />);
  });
}

function rowFor(label: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>('.src__extra')).find((el) =>
    el.textContent?.includes(label),
  );
  if (!row) throw new Error(`no row for ${label}`);
  return row;
}

async function openEditor(row: HTMLElement) {
  const button = Array.from(row.querySelectorAll('button')).find(
    (b) => b.textContent === 'Replace',
  );
  await act(async () => button?.click());
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  setSettingsSecret.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('Tender Impulse card', () => {
  it('shows all three pieces on the one card', async () => {
    await render(tenderImpulse());
    expect(container.textContent).toContain('Encryption key');
    expect(container.textContent).toContain('Starting id');
  });

  it('treats the encryption key as a secret: masked read-back, masked input', async () => {
    await render(tenderImpulse());
    const row = rowFor('Encryption key');
    expect(row.textContent).toContain('Set · …1234');
    await openEditor(row);
    expect(row.querySelector('input')?.type).toBe('password');
  });

  it('reads the starting id back in full, because it is meant to be checked', async () => {
    await render(tenderImpulse());
    const row = rowFor('Starting id');
    expect(row.textContent).toContain('8156393');
    await openEditor(row);
    expect(row.querySelector('input')?.type).toBe('text');
  });

  it('saves the starting id through the settable-values door', async () => {
    await render(tenderImpulse());
    const row = rowFor('Starting id');
    await openEditor(row);
    const input = row.querySelector('input') as HTMLInputElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, '9000000');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const save = Array.from(row.querySelectorAll('button')).find((b) => b.textContent === 'Save');
    await act(async () => save?.click());
    expect(setSettingsSecret).toHaveBeenCalledWith('tender_impulse_start_id', '9000000');
  });

  it('leaves a non-secret second half readable, as before', async () => {
    await render(
      tenderImpulse({
        name: 'spend_network',
        credential_extra_field: 'spend_network_email',
        credential_extra_label: 'Account email',
        credential_extra_value: 'tenders@example.invalid',
        credential_extra_secret: false,
        setup_fields: [],
      }),
    );
    const row = rowFor('Account email');
    expect(row.textContent).toContain('tenders@example.invalid');
    await openEditor(row);
    expect(row.querySelector('input')?.type).toBe('text');
    expect(container.textContent).not.toContain('Starting id');
  });
});
