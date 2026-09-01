import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * The Translate control in the detail drawer.
 *
 * 470 of the stored notices are Brazilian Portuguese, so the whole point of the
 * feature is that a reader can get one of them into English without leaving the
 * panel. The server decides *whether* to offer it (`needs_translation`) because
 * `language` is stored as `en`, `eng`, `English`, `pt` and `French` in
 * production; these tests hold the browser to that decision rather than
 * re-deriving it.
 *
 * The whole DetailPanel is real. Only the API client is stubbed, because the
 * question is what the panel does with an answer - including the two answers
 * that are easy to get wrong, a failure and a notice switched mid-request.
 */

const tenderMock = vi.fn();
const translateMock = vi.fn();

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  api: {
    tender: (...args: unknown[]) => tenderMock(...args),
    translate: (...args: unknown[]) => translateMock(...args),
    setFeedback: vi.fn(),
    clearFeedback: vi.fn(),
  },
}));

let container: HTMLDivElement;
let root: Root;

const PT_TEXT = 'O objeto da presente dispensa de licitação é a escolha da proposta.';
const EN_TEXT = 'The purpose of this bidding exemption is the choice of the proposal.';

function detail(over: Record<string, unknown> = {}) {
  return {
    id: 26,
    source: 'pncp',
    source_notice_id: '0310028.00000022/2026-81',
    source_url: 'https://pncp.gov.br/notice',
    reference_number: '0310028.00000022/2026-81',
    title: 'Contratação de solução corporativa de armazenamento',
    buyer_name: 'CONSELHO REGIONAL DE MEDICINA VETERINARIA',
    buyer_country: 'BR',
    publication_date: '2026-08-20T00:00:00Z',
    deadline: null,
    status: null,
    procurement_stage: null,
    notice_type: null,
    estimated_value: null,
    currency: null,
    relevance_score: 42,
    relevance_category: null,
    fit_status: 'manual_review',
    deployment_fit: 'unknown',
    relevance_reasons: [],
    disqualifiers: [],
    review_flags: [],
    is_actionable: true,
    last_seen_at: '2026-08-30T00:00:00Z',
    first_seen_at: '2026-08-30T00:00:00Z',
    feedback: null,
    auto_irrelevant: false,
    auto_irrelevant_reasons: [],
    hidden: false,
    description: PT_TEXT,
    delivery_location: null,
    classification_codes: [],
    document_urls: [],
    language: 'pt',
    topic_relevance_score: 40,
    product_fit_score: 30,
    procurement_intent_score: 50,
    source_updated_at: null,
    source_timezone: null,
    content_hash: 'hash-26',
    created_at: '2026-08-30T00:00:00Z',
    updated_at: '2026-08-30T00:00:00Z',
    raw_payload: null,
    needs_translation: true,
    ...over,
  };
}

function translation(over: Record<string, unknown> = {}) {
  return {
    tender_id: 26,
    source_language: 'pt',
    target_language: 'en',
    text: EN_TEXT,
    cached: false,
    provider: 'google_free',
    ...over,
  };
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  tenderMock.mockReset();
  translateMock.mockReset();
  tenderMock.mockResolvedValue(detail());
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

async function render(tenderId: number | null = 26) {
  const { DetailPanel } = await import('./DetailPanel');
  await act(async () => {
    root.render(
      <DetailPanel
        bands={{ good_fit: 70, possible_fit: 50 }}
        sourceLabel={(key: string) => key}
        tenderId={tenderId}
        position={{ index: 0, total: 1 }}
        onClose={() => {}}
        onPrev={() => {}}
        onNext={() => {}}
        hasPrev={false}
        hasNext={false}
        onFeedback={() => {}}
      />,
    );
  });
}

/** Re-render with a different notice, the way j/k does - without unmounting. */
async function switchTo(tenderId: number) {
  const { DetailPanel } = await import('./DetailPanel');
  await act(async () => {
    root.render(
      <DetailPanel
        bands={{ good_fit: 70, possible_fit: 50 }}
        sourceLabel={(key: string) => key}
        tenderId={tenderId}
        position={{ index: 1, total: 2 }}
        onClose={() => {}}
        onPrev={() => {}}
        onNext={() => {}}
        hasPrev
        hasNext={false}
        onFeedback={() => {}}
      />,
    );
  });
}

const buttons = () => [...container.querySelectorAll('button')];
const button = (text: string) => buttons().find((b) => b.textContent?.trim() === text);
const click = async (el: Element) => {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
};
const description = () => container.querySelector('.desc')?.textContent ?? '';

describe('the Translate button appears only where it can work', () => {
  it('is offered on a notice the API marked as needing translation', async () => {
    await render();
    expect(button('Translate')).toBeTruthy();
  });

  it('is absent on an English notice', async () => {
    tenderMock.mockResolvedValue(
      detail({
        language: 'eng',
        description: 'Cloud storage framework.',
        needs_translation: false,
      }),
    );
    await render();
    expect(button('Translate')).toBeUndefined();
  });

  it('is absent when there is no description to translate', async () => {
    // No description means no Description section at all, so the button has
    // nowhere to be even though the language is foreign.
    tenderMock.mockResolvedValue(detail({ description: null, needs_translation: false }));
    await render();
    expect(button('Translate')).toBeUndefined();
    expect(container.querySelector('.desc')).toBeNull();
  });

  it('trusts the API rather than re-deriving it from the language code', async () => {
    // A foreign-looking language with the flag off: the server has spoken, and
    // a second opinion in the browser is exactly what this must not grow.
    tenderMock.mockResolvedValue(detail({ language: 'pt', needs_translation: false }));
    await render();
    expect(button('Translate')).toBeUndefined();
  });
});

describe("it looks like the panel's other primary action", () => {
  // Pinned because the first version shipped as a ghost button and read as a
  // caption rather than something to press. The reference is "Open notice" in
  // the same panel: filled, with a trailing icon.
  // An anchor, not a button — it opens a URL. Querying only `button` made an
  // earlier version of this test silently vacuous.
  const notice = () =>
    [...container.querySelectorAll('.btn')].find((el) => el.textContent?.trim() === 'Open notice');

  it('is a filled primary button, not a ghost one', async () => {
    await render();
    const translate = button('Translate')!;
    expect([...translate.classList]).toContain('btn--primary');
    expect([...translate.classList]).not.toContain('btn--ghost');
    expect([...translate.classList]).not.toContain('btn--sm');
  });

  it('carries a trailing icon, like Open notice does', async () => {
    await render();
    const svg = button('Translate')!.querySelector('svg');
    expect(svg).toBeTruthy();
    // Decorative: the meaning is in the label, so it must not be announced.
    expect(svg!.getAttribute('aria-hidden')).toBe('true');
  });

  it('wears exactly the classes the Open notice control does', async () => {
    // A weaker assertion than it looks: this is what stops the two drifting
    // apart the next time either is restyled.
    await render();
    const link = notice();
    expect(link, 'Open notice control not rendered - this test would be vacuous').toBeTruthy();
    expect([...button('Translate')!.classList].sort()).toEqual([...link!.classList].sort());
  });

  it('keeps the filled treatment on the Show original toggle', async () => {
    translateMock.mockResolvedValue(translation());
    await render();
    await click(button('Translate')!);
    expect([...button('Show original')!.classList]).toContain('btn--primary');
  });
});

describe('pressing it', () => {
  it('replaces the description with the English text', async () => {
    translateMock.mockResolvedValue(translation());
    await render();

    expect(description()).toBe(PT_TEXT);
    await click(button('Translate')!);

    expect(translateMock).toHaveBeenCalledWith(26);
    expect(description()).toBe(EN_TEXT);
  });

  it('says where the text came from, and that a machine produced it', async () => {
    translateMock.mockResolvedValue(translation());
    await render();
    await click(button('Translate')!);

    const note = container.querySelector('.translate__note')?.textContent ?? '';
    expect(note).toContain('Portuguese');
    expect(note).toContain('machine');
  });

  it('says "another language" when the API could not name the source', async () => {
    // A real state since D33 was amended: the button is now offered on text
    // nothing could confidently identify, and the provider is asked to detect
    // the language itself. When neither it nor the feed names one, the API
    // sends an empty string rather than a guess, and the caption has to stay a
    // sentence. Naming a language a reader cannot check, underneath a
    // translation they cannot check, is the failure this avoids.
    translateMock.mockResolvedValue(translation({ source_language: '' }));
    await render();
    await click(button('Translate')!);

    const note = container.querySelector('.translate__note')?.textContent ?? '';
    expect(note).toContain('Translated from another language by machine');
  });

  it('reports progress on the button while the request is in flight', async () => {
    let release: (value: unknown) => void = () => {};
    translateMock.mockReturnValue(new Promise((resolve) => (release = resolve)));
    await render();

    await click(button('Translate')!);
    expect(button('Translating…')).toBeTruthy();
    expect(button('Translating…')!.disabled).toBe(true);

    await act(async () => {
      release(translation());
    });
    expect(description()).toBe(EN_TEXT);
  });

  it('does not call the API twice for the same notice', async () => {
    translateMock.mockResolvedValue(translation());
    await render();
    await click(button('Translate')!);

    // The button is gone once there is a translation - the toggle takes its
    // place, and the toggle asks the server nothing.
    expect(button('Translate')).toBeUndefined();
    await click(button('Show original')!);
    await click(button('Show English')!);
    expect(translateMock).toHaveBeenCalledTimes(1);
  });
});

describe('the original stays reachable', () => {
  it('toggles back and forth without another request', async () => {
    translateMock.mockResolvedValue(translation());
    await render();
    await click(button('Translate')!);
    expect(description()).toBe(EN_TEXT);

    await click(button('Show original')!);
    expect(description()).toBe(PT_TEXT);
    expect(container.querySelector('.translate__note')?.textContent).toContain('Original text');

    await click(button('Show English')!);
    expect(description()).toBe(EN_TEXT);
    expect(translateMock).toHaveBeenCalledTimes(1);
  });
});

describe('when it fails', () => {
  it('keeps the original text and says why', async () => {
    const { ApiError } = await import('../api/client');
    translateMock.mockRejectedValue(
      new (ApiError as unknown as new (m: string, s: number) => Error)(
        'Could not reach the translation service.',
        502,
      ),
    );
    await render();
    await click(button('Translate')!);

    // The reader must not be left with an empty description because a third
    // party was down.
    expect(description()).toBe(PT_TEXT);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      'Could not reach the translation service.',
    );
  });

  it('can be pressed again', async () => {
    const { ApiError } = await import('../api/client');
    translateMock.mockRejectedValueOnce(
      new (ApiError as unknown as new (m: string, s: number) => Error)('Temporary failure.', 502),
    );
    translateMock.mockResolvedValueOnce(translation());
    await render();

    await click(button('Translate')!);
    expect(button('Translate')).toBeTruthy();

    await click(button('Translate')!);
    expect(description()).toBe(EN_TEXT);
    expect(translateMock).toHaveBeenCalledTimes(2);
  });
});

describe('stepping to another notice', () => {
  it('clears the previous notice English', async () => {
    translateMock.mockResolvedValue(translation());
    await render();
    await click(button('Translate')!);
    expect(description()).toBe(EN_TEXT);

    const other = 'Outro objeto totalmente diferente.';
    tenderMock.mockResolvedValue(detail({ id: 27, description: other }));
    await switchTo(27);

    expect(description()).toBe(other);
    expect(button('Translate')).toBeTruthy();
    expect(button('Show original')).toBeUndefined();
  });

  it('drops an answer that arrives after the reader has moved on', async () => {
    // The bug this pins: j/k switches notices while a translation is in
    // flight, and the late reply lands on whatever is now on screen.
    let release: (value: unknown) => void = () => {};
    translateMock.mockReturnValue(new Promise((resolve) => (release = resolve)));
    await render();
    await click(button('Translate')!);

    const other = 'Outro objeto totalmente diferente.';
    tenderMock.mockResolvedValue(detail({ id: 27, description: other }));
    await switchTo(27);

    await act(async () => {
      release(translation({ tender_id: 26 }));
    });

    expect(description()).toBe(other);
    expect(description()).not.toBe(EN_TEXT);
  });
});
