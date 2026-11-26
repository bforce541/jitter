const byId = (id) => document.getElementById(id);
const display = Object.fromEntries(
  ['play', 'back', 'next', 'state', 'sequence', 'buffered', 'packets', 'clock', 'event', 'chapters', 'audit-rows']
    .map((id) => [id, byId(id)]),
);
const labels = {
  current: 'Current',
  'waiting-gap': 'Waiting for gap',
  stale: 'Stale',
  recovering: 'Recovering',
};

function initialize(replay) {
  const { timeline, publications, recovery, audit } = replay;
  const finalStep = timeline.length - 1;
  const gap = timeline.findIndex(({ state }) => state === 'stale');
  const recovered = timeline.findIndex(({ sequence }) => sequence === recovery.last_verified);
  const milestones = [
    ['Start', 0],
    ['Feed B', timeline.findIndex(({ sequence }) => sequence === 2)],
    ['Gap', gap],
    ['Snapshot', recovered],
    ['Verified', finalStep],
  ];
  const bySequence = new Map(publications.map((row) => [row.sequence, row]));
  const packets = Array.from({ length: timeline[finalStep].sequence }, (_, index) => {
    const tile = document.createElement('li');
    tile.textContent = String(index + 1).padStart(2, '0');
    display.packets.append(tile);
    return tile;
  });
  let selected = gap;
  let timer;
  let paused = false;

  const chapterButtons = milestones.map(([label, index]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', () => select(index));
    display.chapters.append(button);
    return button;
  });

  function packetState(sequence, step) {
    const item = bySequence.get(sequence);
    if (sequence === recovery.snapshot_through && selected >= gap) {
      return selected >= recovered ? 'snapshot' : 'missing';
    }
    if (sequence > step.sequence || !item) return 'pending';
    if (item.replayed) return 'restored';
    return item.channel === 'B' ? 'backup' : 'published';
  }

  const packetLabels = {
    published: 'Published by feed A',
    backup: 'Published by feed B',
    missing: 'Missing from both feeds',
    restored: 'Replayed after snapshot',
    snapshot: 'Covered by snapshot',
    pending: 'Not yet published',
  };

  function render() {
    const step = timeline[selected];
    display.state.textContent = labels[step.state];
    display.state.dataset.state = step.state;
    display.sequence.textContent = String(step.sequence).padStart(2, '0');
    display.buffered.textContent = String(step.backlog).padStart(2, '0');
    display.clock.textContent = `${(step.elapsed_ns / 1e6).toFixed(3)} ms`;
    const event = step.operation[0].toUpperCase() + step.operation.slice(1);
    display.event.textContent = selected === finalStep
      ? `Replay complete. Book matches the reference at sequence ${step.sequence}.`
      : `${event}.`;
    packets.forEach((tile, index) => {
      const sequence = index + 1;
      const state = packetState(sequence, step);
      tile.dataset.packet = state;
      tile.setAttribute('aria-label', `Sequence ${sequence}: ${packetLabels[state]}`);
    });
    const chapter = milestones.findLastIndex(([, index]) => selected >= index);
    chapterButtons.forEach((button, index) => {
      button.setAttribute('aria-current', index === chapter ? 'step' : 'false');
    });
    display.back.disabled = selected === 0;
    display.next.disabled = selected === finalStep;
    display.play.textContent = timer ? 'Pause replay' : paused ? 'Resume replay' : 'Play from start';
  }

  function stop() {
    clearInterval(timer);
    timer = undefined;
  }

  function select(index) {
    stop();
    paused = false;
    selected = index;
    render();
  }

  function play(fromStart = true) {
    stop();
    paused = false;
    if (fromStart) selected = 0;
    timer = setInterval(() => {
      selected += 1;
      if (selected === finalStep) stop();
      render();
    }, 1050);
    render();
  }

  display.play.addEventListener('click', () => {
    if (timer) {
      stop();
      paused = true;
      render();
    } else {
      play(!paused);
    }
  });
  display.back.addEventListener('click', () => select(selected - 1));
  display.next.addEventListener('click', () => select(selected + 1));

  display['audit-rows'].replaceChildren(...audit.map(({ as_of_ns, state, positionImpact }) => {
    const row = document.createElement('tr');
    row.dataset.audit = state;
    const impact = positionImpact.ABC;
    for (const label of [`${as_of_ns / 1e6} ms`, state, String(impact).replace('-', '−')]) {
      const cell = document.createElement('td');
      cell.textContent = label;
      row.append(cell);
    }
    return row;
  }));

  display.play.disabled = false;
  render();
}

try {
  const response = await fetch('/replay.json');
  if (!response.ok) throw new Error('Replay data is unavailable.');
  initialize(await response.json());
} catch (error) {
  display.state.textContent = 'Unavailable';
  display.event.textContent = 'The replay could not load. Refresh the page to try again.';
  console.error(error);
}
