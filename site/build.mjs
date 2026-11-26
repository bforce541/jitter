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
