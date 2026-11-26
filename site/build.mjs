import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';

const output = new URL('../dist/jitter-site/', import.meta.url);
const source = new URL('../docs/evidence/desk/incident-arm64.json', import.meta.url);
const { market, desk } = JSON.parse(await readFile(source, 'utf8'));

if (!market.dual || !market.book_verified || !desk.verified || !desk.idempotent || desk.result.open.length) {
  throw new Error('The showcase requires a verified dual-feed incident and execution audit.');
}

const replay = {
  timeline: market.timeline.map(({ state, operation, backlog, sequence, elapsed_ns }) => ({
    state, operation, backlog, sequence, elapsed_ns,
  })),
  publications: market.publications,
  recovery: market.recovery_proof,
  finalBookId: market.final_book_id,
  referenceBookId: market.reference_book_id,
  duplicatePublications: market.duplicate_publications,
  audit: desk.result.history.map(({ as_of_ns, execution_id, state, evidence }) => ({
    as_of_ns, execution_id, state, positionImpact: evidence.position_impact,
  })),
  positions: desk.result.positions,
};

await mkdir(output, { recursive: true });
await cp(new URL('./public/', import.meta.url), output, { recursive: true });
await writeFile(new URL('replay.json', output), JSON.stringify(replay, null, 2) + '\n');
const itch = JSON.parse(await readFile(new URL('../docs/evidence/itch/showcase.json', import.meta.url), 'utf8'));
const linux = JSON.parse(await readFile(new URL('../docs/evidence/itch/linux-vm.json', import.meta.url), 'utf8'));
if (linux.tape_sha256 !== itch.tape.sha256) throw new Error('Linux evidence uses a different historical tape.');
itch.linux = {
  environment: linux.environment,
  offeredRate: linux.studies.open.runs[0].sender.rate,
  run: linux.run,
  windows: ['open', 'quiet'].map((window) => {
    const study = linux.studies[window];
    if (!study.runs.every((run) => run.verified)) throw new Error(`Unverified ${window} transport trial.`);
    const baseline = study.summary['clean/packet-8/read-1'];
    const batched = study.summary['clean/packet-8/read-16'];
    return { window, baseline: baseline.scheduled_to_publication_ns.p999,
      batched: batched.scheduled_to_publication_ns.p999 };
  }),
};
await writeFile(new URL('itch-evidence.json', output), JSON.stringify(itch, null, 2) + '\n');
