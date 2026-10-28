import type {
  BrainConnection,
  ConnectionState,
} from '../src/connection/BrainConnection';
import type { AudioDevice, DeviceStt, DeviceTts } from '../src/voice/devices';
import { VoiceController } from '../src/voice/VoiceController';
import { WakeController } from '../src/wake/WakeController';
import type { WakeWordDetector } from '../src/wake/WakeWordDetector';

class Conn {
  state: ConnectionState = 'connected';
  sent: Record<string, any>[] = [];
  ml: ((m: any) => void)[] = [];
  sl: ((s: ConnectionState) => void)[] = [];
  send(m: Record<string, any>) {
    this.sent.push(m);
    return true;
  }
  onMessage(l: (m: any) => void) {
    this.ml.push(l);
    return () => undefined;
  }
  onState(l: (s: ConnectionState) => void) {
    this.sl.push(l);
    return () => undefined;
  }
  receive(m: Record<string, any>) {
    this.ml.forEach(l => l(m));
  }
  of(type: string) {
    return this.sent.filter(m => m.type === type);
  }
}

class Audio implements AudioDevice {
  log: string[] = [];
  chunk: ((d: string) => void) | null = null;
  stopRequested: (() => void) | null = null;
  headset = false;
  failService = false;
  async start(rate: number) {
    this.log.push(`start:${rate}`);
  }
  async stop(keep?: boolean) {
    this.log.push(keep ? 'stop:keep' : 'stop');
  }
  async startService(title: string) {
    if (this.failService) {
      throw new Error('no permission');
    }
    this.log.push(`service:${title}`);
  }
  async stopService() {
    this.log.push('service:stop');
  }
  onChunk(l: (d: string) => void) {
    this.chunk = l;
    return () => undefined;
  }
  onStopRequested(l: () => void) {
    this.stopRequested = l;
    return () => {
      this.stopRequested = null;
    };
  }
  async hasHeadset() {
    return this.headset;
  }
  async applyRoute() {}
  onRouteChange() {
    return () => undefined;
  }
  async pauseCapture() {}
  async resumeCapture() {}
  async openPlayer() {}
  play() {}
  flush() {}
  async closePlayer() {}
}

class Detector implements WakeWordDetector {
  available = true;
  running = false;
  onWake: (() => void) | null = null;
  onError: ((m: string) => void) | null = null;
  events: string[] = [];
  failStart = false;
  async start(onWake: () => void, onError: (m: string) => void) {
    if (this.failStart) {
      throw new Error('boom');
    }
    this.running = true;
    this.onWake = onWake;
    this.onError = onError;
    this.events.push('start');
  }
  async stop() {
    this.running = false;
    this.events.push('stop');
  }
}

const tts: DeviceTts = { available: true, speak: jest.fn(), stop: jest.fn() };
const stt: DeviceStt = {
  available: false,
  start: jest.fn(),
  stop: jest.fn(async () => undefined),
};
const tick = () => new Promise(resolve => setImmediate(resolve));

const READY = {
  type: 'voice.ready',
  reply_to: 'v',
  input: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
  output: { encoding: 'pcm_s16le', sample_rate: 24000, channels: 1 },
  stt: 'cloud',
  tts: 'cloud',
};

function setup() {
  const conn = new Conn();
  const audio = new Audio();
  const detector = new Detector();
  let n = 0;
  const voice = new VoiceController(
    conn as unknown as BrainConnection,
    audio,
    tts,
    stt,
    () => `v-${(n += 1)}`,
  );
  const wake = new WakeController(
    conn as unknown as BrainConnection,
    voice,
    audio,
    detector,
  );
  return { conn, audio, detector, voice, wake };
}

test('enabling starts the service and waits for the word: nothing goes to the brain', async () => {
  const { conn, audio, detector, wake } = setup();
  expect(await wake.enable()).toBe(true);
  expect(wake.state.phase).toBe('standby');
  expect(audio.log).toEqual(['service:Juniper está esperando']);
  expect(detector.running).toBe(true);
  audio.chunk?.('AAAA');
  expect(conn.sent).toEqual([]); // standby: the microphone feeds only the detector
});

test('hearing the word frees the microphone and opens a woken session (the brain greets)', async () => {
  const { conn, audio, detector, wake } = setup();
  await wake.enable();
  detector.onWake?.();
  await tick();
  await tick();
  expect(detector.events).toEqual(['start', 'stop']); // the detector let go first
  expect(audio.log).toContain('start:16000'); // then the session captured the microphone
  expect(conn.of('voice.start')[0]).toMatchObject({ wake: true });
  expect(wake.state.phase).toBe('session');
});

test('the brain ending the session (music playing) goes back to waiting for the word', async () => {
  const { conn, audio, detector, voice, wake } = setup();
  await wake.enable();
  detector.onWake?.();
  await tick();
  conn.receive(READY);
  await tick();
  conn.receive({ type: 'voice.end', reason: 'playback' });
  await tick();
  await tick();
  expect(voice.state.phase).toBe('off');
  expect(voice.state.notice).toBeNull(); // closing after the music is on purpose, quiet
  expect(audio.log).toContain('stop:keep'); // the service stays up: the word is being listened for
  expect(wake.state.phase).toBe('standby');
  expect(detector.running).toBe(true); // listening again
  expect(detector.events).toEqual(['start', 'stop', 'start']);
});

test('a quiet session (no request) also returns to waiting', async () => {
  const { conn, detector, wake } = setup();
  await wake.enable();
  detector.onWake?.();
  await tick();
  conn.receive(READY);
  await tick();
  conn.receive({ type: 'voice.end', reason: 'silence' });
  await tick();
  await tick();
  expect(wake.state.phase).toBe('standby');
  expect(detector.running).toBe(true);
});

test('the same word twice at once opens one session only', async () => {
  const { conn, detector, wake } = setup();
  await wake.enable();
  detector.onWake?.();
  detector.onWake?.();
  await tick();
  await tick();
  expect(conn.of('voice.start')).toHaveLength(1);
});

test('without a connection the session does not open and it keeps waiting', async () => {
  const { conn, detector, wake } = setup();
  await wake.enable();
  conn.state = 'offline';
  detector.onWake?.();
  await tick();
  await tick();
  expect(conn.of('voice.start')).toEqual([]);
  expect(wake.state.phase).toBe('standby');
  expect(detector.running).toBe(true);
});

test('no AccessKey: it says what is missing and does nothing', async () => {
  const { audio, detector, wake } = setup();
  detector.available = false;
  expect(await wake.enable()).toBe(false);
  expect(wake.state.phase).toBe('off');
  expect(wake.state.notice).toContain('chave da Picovoice');
  expect(audio.log).toEqual([]);
});

test('no microphone permission in the background: it does not start', async () => {
  const { audio, wake } = setup();
  audio.failService = true;
  expect(await wake.enable()).toBe(false);
  expect(wake.state).toMatchObject({ phase: 'off' });
  expect(wake.state.notice).toContain('microfone');
});

test('a detector that fails to start turns the mode off with a hint, not a crash', async () => {
  const { audio, detector, wake } = setup();
  detector.failStart = true;
  expect(await wake.enable()).toBe(false);
  expect(wake.state.phase).toBe('off');
  expect(wake.state.notice).toContain('juniper.ppn');
  expect(audio.log).toContain('service:stop');
});

test('an engine error while waiting turns the mode off', async () => {
  const { detector, wake } = setup();
  await wake.enable();
  detector.onError?.('invalid access key');
  await tick();
  await tick();
  expect(wake.state.phase).toBe('off');
  expect(wake.state.notice).toBeTruthy();
});

test('the notification "Parar" while waiting turns the mode off', async () => {
  const { audio, detector, wake } = setup();
  await wake.enable();
  audio.stopRequested?.();
  await tick();
  await tick();
  expect(wake.state.phase).toBe('off');
  expect(detector.running).toBe(false);
  expect(audio.log).toContain('service:stop');
});

test('disabling in the middle of a session ends it and releases everything', async () => {
  const { conn, audio, detector, voice, wake } = setup();
  await wake.enable();
  detector.onWake?.();
  await tick();
  conn.receive(READY);
  await tick();
  await wake.disable();
  expect(conn.of('voice.end')).toEqual([
    { type: 'voice.end', reason: 'client' },
  ]);
  expect(voice.state.phase).toBe('off');
  expect(audio.log.at(-1)).toBe('service:stop');
  expect(detector.running).toBe(false);
});

// --- the microphone is closed whenever she is not waiting for him ---------------------------

async function openSession() {
  const s = setup();
  await s.voice.start();
  s.conn.receive(READY);
  await tick();
  return s;
}

test.each(['thinking', 'speaking'])(
  'while she is %s the microphone sends nothing (speaker)',
  async state => {
    const { conn, audio } = await openSession();
    conn.receive({ type: 'voice.state', state: 'listening' });
    audio.chunk?.('OPEN');
    conn.receive({ type: 'voice.state', state, turn_id: 't-1' });
    audio.chunk?.('CLOSED');
    conn.receive({ type: 'voice.state', state: 'listening' });
    audio.chunk?.('OPEN AGAIN');
    expect(conn.of('voice.audio').map(m => m.data)).toEqual([
      'OPEN',
      'OPEN AGAIN',
    ]);
  },
);

test('with headphones the microphone stays open so he can still cut her off', async () => {
  const s = setup();
  s.audio.headset = true;
  await s.voice.start();
  s.conn.receive(READY);
  await tick();
  s.conn.receive({ type: 'voice.state', state: 'speaking', turn_id: 't-1' });
  s.audio.chunk?.('OVER HER');
  expect(s.conn.of('voice.audio').map(m => m.data)).toEqual(['OVER HER']);
});

test('the playback end closes quietly without a notice', async () => {
  const { conn, voice } = await openSession();
  conn.receive({ type: 'voice.end', reason: 'playback' });
  await tick();
  expect(voice.state).toMatchObject({ phase: 'off', notice: null });
});
