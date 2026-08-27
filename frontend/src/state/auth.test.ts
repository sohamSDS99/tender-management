import { describe, expect, it } from 'vitest';
import { describeAgent, initials, inviteFromSearch, withoutInvite } from './auth';

describe('inviteFromSearch', () => {
  it('reads the token an invitation link carries', () => {
    expect(inviteFromSearch('?invite=abc123')).toBe('abc123');
    expect(inviteFromSearch('?minimum_score=70&invite=abc123')).toBe('abc123');
  });

  it('is null when the reader arrived ordinarily', () => {
    expect(inviteFromSearch('')).toBeNull();
    expect(inviteFromSearch('?minimum_score=70')).toBeNull();
  });

  it('treats an empty or blank token as no token', () => {
    // Otherwise the dialog opens in register mode holding '' and the API
    // answers "not a valid invitation" to somebody who was never invited.
    expect(inviteFromSearch('?invite=')).toBeNull();
    expect(inviteFromSearch('?invite=%20%20')).toBeNull();
  });
});

describe('withoutInvite', () => {
  it('takes the token out and leaves every filter alone', () => {
    const out = withoutInvite('minimum_score=70&invite=secret&sort=score_desc');
    const params = new URLSearchParams(out);
    expect(params.has('invite')).toBe(false);
    expect(params.get('minimum_score')).toBe('70');
    expect(params.get('sort')).toBe('score_desc');
  });

  it('empties a query string that held nothing else', () => {
    expect(withoutInvite('invite=secret')).toBe('');
  });

  it('leaves a URL that never had one untouched', () => {
    expect(withoutInvite('minimum_score=70')).toBe('minimum_score=70');
  });

  it('preserves the Slack digest deep link, which sign-in must not eat', () => {
    // D26 claims a `?tender=` link survives being sent to the gate: the page
    // does not navigate or reload, so the parameter is still there when the
    // Dashboard finally mounts and reads it. The one thing that rewrites the
    // URL on that path is this function, stripping a spent invite token — so
    // this is where that claim can actually break.
    const out = withoutInvite('tender=4821&invite=secret&minimum_score=70');
    const params = new URLSearchParams(out);
    expect(params.get('tender')).toBe('4821');
    expect(params.get('minimum_score')).toBe('70');
    expect(params.has('invite')).toBe(false);
  });
});

describe('initials', () => {
  it('takes the first and last word of a name', () => {
    expect(initials({ display_name: 'Ada Lovelace', email: 'ada@example.com' })).toBe('AL');
    expect(initials({ display_name: 'Ada King Lovelace', email: 'ada@example.com' })).toBe('AL');
  });

  it('falls back through one word, then the address', () => {
    expect(initials({ display_name: 'Ada', email: 'ada@example.com' })).toBe('AD');
    expect(initials({ display_name: '', email: 'ada@example.com' })).toBe('AD');
  });

  it('is never empty for a real account, because an empty chip reads as broken', () => {
    expect(initials({ display_name: 'X', email: 'x@example.com' })).toBe('X');
    expect(initials({ display_name: '   ', email: 'q@example.com' }).length).toBeGreaterThan(0);
  });

  it('is empty only when nobody is signed in', () => {
    expect(initials(null)).toBe('');
  });
});

describe('describeAgent', () => {
  it('names the browser and the platform a session is on', () => {
    const chromeOnMac =
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36';
    expect(describeAgent(chromeOnMac)).toBe('Chrome on macOS');
  });

  it('does not call Edge or Opera "Chrome", though both say Chrome', () => {
    // Both ship a Chrome token in their UA. Matching Chrome first would label
    // every Edge session Chrome, and the whole point of this string is that
    // somebody recognises their own browser in it.
    expect(
      describeAgent('Mozilla/5.0 (Windows NT 10.0) Chrome/131.0 Safari/537.36 Edg/131.0'),
    ).toBe('Edge on Windows');
    expect(
      describeAgent('Mozilla/5.0 (Windows NT 10.0) Chrome/131.0 Safari/537.36 OPR/117.0'),
    ).toBe('Opera on Windows');
  });

  it('does not call Chrome "Safari", though Chrome says Safari', () => {
    expect(
      describeAgent('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Version/17.0 Safari/604.1'),
    ).toBe('Safari on iOS');
  });

  it('says something rather than nothing for an unrecognised agent', () => {
    expect(describeAgent('curl/8.4.0')).toBe('curl/8.4.0');
    expect(describeAgent('')).toBe('Unknown browser');
    expect(describeAgent('   ')).toBe('Unknown browser');
  });

  it('truncates an unrecognised agent rather than widening the row', () => {
    expect(describeAgent('z'.repeat(200)).length).toBeLessThanOrEqual(40);
  });
});
