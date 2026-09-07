/**
 * Calibrate QUIET_RUNS and QUIET_FLOOR against real fetch_runs history.
 *
 * The two floors in `labels.ts` are estimates. This replays the *shipped* rule
 * over real history and counts the alarms it would have raised, because a
 * warning that fires on a healthy source is worse than no warning at all: the
 * reader learns to ignore it, and then the quiet source is invisible again.
 *
 * It imports sourceVolume rather than restating it, so the thing being measured
 * is the thing that ships.
 *
 * Usage — no new dependency; vite-node arrives with vitest:
 *
 *   npx vite-node scripts/calibrate-quiet.ts -- runs.json
 *
 * where runs.json is a JSON array of fetch_runs rows, newest first, each with
 * at least: source, status, trigger, records_received, started_at.
 */
import { readFileSync } from 'node:fs';
import { QUIET_FLOOR, QUIET_RUNS, sourceVolume, type VolumeRun } from '../src/labels';

interface Row extends VolumeRun {
  started_at?: string;
}

const path = process.argv[2];
if (!path) {
  console.error('usage: npx vite-node scripts/calibrate-quiet.ts -- <runs.json>');
  process.exit(2);
}

const rows: Row[] = JSON.parse(readFileSync(path, 'utf-8'));
// Newest first is what /api/fetch-runs returns and what the rule expects. Sort
// defensively: a dump straight from psql may well arrive the other way round.
rows.sort((a, b) => String(b.started_at ?? '').localeCompare(String(a.started_at ?? '')));

const sources = [...new Set(rows.map((row) => row.source))].sort();

console.log(`QUIET_RUNS=${QUIET_RUNS}  QUIET_FLOOR=${QUIET_FLOOR}`);
console.log(`${rows.length} runs, ${sources.length} sources`);
console.log(
  `window ${rows.at(-1)?.started_at ?? '?'} .. ${rows[0]?.started_at ?? '?'}\n`,
);

let firing = 0;
let episodes = 0;

for (const source of sources) {
  const cron = rows.filter(
    (row) =>
      row.source === source &&
      row.trigger === 'cron' &&
      (row.status === 'success' || row.status === 'partial'),
  );
  const counts = cron.map((row) => row.records_received);
  const verdict = sourceVolume(source, rows);

  /*
   * Replay: rows.slice(i) is the history as it stood when run i was the newest,
   * so walking i backwards is what the dashboard would have shown over time.
   * Count rising edges, not quiet days — one outage is one warning.
   */
  let wasQuiet = false;
  let sourceEpisodes = 0;
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const nowQuiet = sourceVolume(source, rows.slice(i)).verdict === 'quiet';
    if (nowQuiet && !wasQuiet) sourceEpisodes += 1;
    wasQuiet = nowQuiet;
  }

  if (verdict.verdict === 'quiet') firing += 1;
  episodes += sourceEpisodes;

  const zeroRuns = counts.filter((n) => n === 0).length;
  console.log(
    [
      source.padEnd(18),
      `cron=${String(cron.length).padStart(3)}`,
      `zero=${String(zeroRuns).padStart(3)}`,
      `min=${String(Math.min(...counts, 0)).padStart(4)}`,
      `max=${String(Math.max(...counts, 0)).padStart(5)}`,
      `now=${verdict.verdict.padEnd(7)}`,
      `episodes=${sourceEpisodes}`,
    ].join('  '),
  );
}

console.log(`\nfiring right now: ${firing} of ${sources.length}`);
console.log(`alarm episodes across the whole window: ${episodes}`);
console.log(
  '\nRead it like this: any source you believe is healthy that shows now=quiet, or a\n' +
    'source with several episodes, means the floors are too low. Raise QUIET_RUNS\n' +
    '(longer silence before speaking) or QUIET_FLOOR (ignore low-volume sources).',
);
