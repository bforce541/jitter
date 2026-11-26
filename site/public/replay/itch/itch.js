const get = (id) => document.getElementById(id);
const format = new Intl.NumberFormat('en-US');
const milliseconds = (nanoseconds) => (nanoseconds / 1e6).toFixed(2);

function initialize(evidence) {
  const { session, tape, transport, window } = evidence;
  const [initial, gap, request, recovery] = transport.timeline;
  const { final_sequence: final, snapshot_through: snapshot } = transport;
  const steps = [
    ['Start', initial, 'Current', 0,
      'Both runs begin from the same reference snapshot before the opening window.'],
    ['Gap', gap, 'Stale', gap.buffered,
      `A loses ${transport.dropped_datagrams_a} datagrams and B loses ${transport.dropped_datagrams_b}. The same ${transport.snapshot_covered} messages are missing, so publication stops at ${format.format(gap.sequence)}.`],
    ['Buffer', request, 'Stale', request.buffered,
      `New updates keep arriving while the receiver builds a snapshot through sequence ${format.format(snapshot)}.`],
    ['Recover', recovery, 'Current', 0,
      `The receiver validates and applies ${format.format(transport.snapshot_buffered_suffix)} buffered messages before resuming publication.`],
    ['Verify', { sequence: final, received_through: final }, 'Verified', 0,
      'At the final sequence, every live displayed order matches the independent reference in both runs.'],
  ];

  get('tape-window').textContent = `${tape.date} · ${format.format(window.messages)} messages`;
  get('messages').textContent = format.format(session.messages);
  get('symbols').textContent = format.format(session.directory_symbols);
  get('checks').textContent = format.format(session.reference_quote_checks);
  const environment = transport.environment === 'macos-arm64' ? 'macOS ARM64 loopback' : transport.environment;
  get('scope').textContent = `Recorded on ${environment}. The stale-to-recovery interval was ${milliseconds(transport.recovery_ns)} ms in this run. The left sequence aligns with the faulted run’s received-through position.`;

  let active = 1;
  let timer;
  let paused = false;
  const tabs = steps.map(([label], index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', () => {
      stop();
      paused = false;
      active = index;
      render();
    });
    get('steps').append(button);
    return button;
  });

  function stop() {
    clearInterval(timer);
    timer = undefined;
  }

  function render() {
    const [, step, state, buffered, note] = steps[active];
    const verified = active === steps.length - 1;
    tabs.forEach((tab, index) => tab.setAttribute('aria-current', index === active ? 'step' : 'false'));
    get('reference-sequence').textContent = format.format(step.received_through);
    get('fault-sequence').textContent = format.format(step.sequence);
    get('fault-state').textContent = state;
    get('fault-state').dataset.state = state === 'Stale' ? 'stale' : 'current';
    get('buffered').textContent = format.format(buffered);
    get('reference-hash').textContent = verified ? transport.uninterrupted_fingerprint : '—';
    get('fault-hash').textContent = verified ? transport.recovered_fingerprint : '—';
    get('note').textContent = note;
    get('previous').disabled = active === 0;
    get('next').disabled = verified;
    get('play').textContent = timer ? 'Pause replay' : paused ? 'Resume replay' : 'Play from start';
  }

  get('previous').addEventListener('click', () => tabs[active - 1].click());
  get('next').addEventListener('click', () => tabs[active + 1].click());
  get('play').addEventListener('click', () => {
    if (timer) {
      stop();
      paused = true;
    } else {
      if (!paused) active = 0;
      paused = false;
      timer = setInterval(() => {
        active += 1;
        if (active === steps.length - 1) stop();
        render();
      }, 1400);
    }
    render();
  });
  get('play').disabled = false;
  render();
}

try {
  const response = await fetch('/itch-evidence.json');
  if (!response.ok) throw new Error('historical evidence is unavailable');
  initialize(await response.json());
} catch (error) {
  get('fault-state').textContent = 'Unavailable';
  get('note').textContent = 'The comparison could not load. Refresh the page to try again.';
  console.error(error);
}
