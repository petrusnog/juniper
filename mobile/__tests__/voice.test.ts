import type {
  BrainConnection,
  ConnectionState,
} from '../src/connection/BrainConnection';
import type { AudioDevice, DeviceStt, DeviceTts } from '../src/voice/devices';
import { VoiceController } from '../src/voice/VoiceController';

class FakeConnection {
  state: ConnectionState = 'connected';
  sent: Record<string, any>[] = [];
  up = true;
  messageListener: ((m: any) => void) | null = null;
  stateListener: ((s: ConnectionState) => void) | null = null;
  send(m: Record<string, any>) {
    if (!this.up) {
      return false;
    }
    this.sent.push(m);
    return true;
  }
  onMessage(l: (m: any) => void) {
    this.messageListener = l;
    return () => {
      this.messageListener = null;
    };
  }
  onState(l: (s: ConnectionState) => void) {
    this.stateListener = l;
    return () => {
      this.stateListener = null;
    };
  }
  receive(m: Record<string, any>) {
    this.messageListener?.(m);
  }
  sentOfType(type: string) {
    return this.sent.filter(m => m.type === type);
  }
}

class FakeAudio implements AudioDevice {
  log: string[] = [];
  played: string[] = [];
  chunk: ((d: string) => void) | null = null;
  stopRequested: (() => void) | null = null;
  failStart = false;
  async start(rate: number) {
    if (this.failStart) {
      throw new Error('no permission');
    }
    this.log.push(`start:${rate}`);
  }
  async stop() {
    this.log.push('stop');
  }
  onChunk(l: (d: string) => void) {
    this.chunk = l;
    return () => {
      this.chunk = null;
    };
  }
  onStopRequested(l: () => void) {
    this.stopRequested = l;
    return () => {
      this.stopRequested = null;
    };
  }
  async openPlayer(rate: number) {
    this.log.push(`player:${rate}`);
  }
  play(d: string) {
    this.played.push(d);
  }
  flush() {
    this.log.push('flush');
  }
  async closePlayer() {
    this.log.push('closePlayer');
  }
}

class FakeTts implements DeviceTts {
  available = true;
  said: string[] = [];
  stops = 0;
  speak(t: string) {
    this.said.push(t);
  }
  stop() {
    this.stops += 1;
  }
}

class FakeStt implements DeviceStt {
  available = true;
  onText: ((t: string) => void) | null = null;
  starts = 0;
  stops = 0;
  async start(onText: (t: string) => void) {
    this.starts += 1;
    this.onText = onText;
  }
  async stop() {
    this.stops += 1;
  }
}

const READY = (stt = 'cloud', tts = 'cloud') => ({
  type: 'voice.ready',
  reply_to: 'v',
  input: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
  output: { encoding: 'pcm_s16le', sample_rate: 24000, channels: 1 },
  stt,
  tts,
});

function setup() {
  const connection = new FakeConnection();
  const audio = new FakeAudio();
  const tts = new FakeTts();
  const stt = new FakeStt();
  let n = 0;
  const voice = new VoiceController(
    connection as unknown as BrainConnection,
    audio,
    tts,
    stt,
    () => `v-${(n += 1)}`,
  );
  return { connection, audio, tts, stt, voice };
}

async function open(stt = 'cloud', tts = 'cloud') {
  const s = setup();
  await s.voice.start();
  s.connection.receive(READY(stt, tts));
  await new Promise(resolve => setImmediate(resolve));
  return s;
}

test('start opens the microphone, asks the brain for a session and waits for it', async () => {
  const { connection, audio, voice } = setup();
  expect(await voice.start()).toBe(true);
  expect(audio.log).toEqual(['start:16000']);
  expect(connection.sent[0]).toEqual({
    type: 'voice.start',
    id: 'v-1',
    format: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
    local_stt: true,
    local_tts: true,
  });
  expect(voice.state.phase).toBe('starting');
});

test('ready opens the player at the rate the brain declared and starts listening', async () => {
  const { audio, voice } = await open();
  expect(audio.log).toContain('player:24000');
  expect(voice.state).toMatchObject({
    phase: 'listening',
    stt: 'cloud',
    tts: 'cloud',
  });
});

test('microphone chunks go to the brain in order, only while the cloud STT is in use', async () => {
  const { connection, audio } = await open();
  audio.chunk?.('AAAA');
  audio.chunk?.('BBBB');
  expect(connection.sentOfType('voice.audio')).toEqual([
    { type: 'voice.audio', seq: 0, data: 'AAAA' },
    { type: 'voice.audio', seq: 1, data: 'BBBB' },
  ]);
});

test('chunks before the session is ready, or while offline, are dropped and never queued', async () => {
  const s = setup();
  await s.voice.start();
  s.audio.chunk?.('EARLY');
  expect(s.connection.sentOfType('voice.audio')).toEqual([]);
  s.connection.receive(READY());
  await new Promise(resolve => setImmediate(resolve));
  s.connection.up = false;
  s.audio.chunk?.('LOST');
  s.connection.up = true;
  s.audio.chunk?.('OK');
  expect(s.connection.sentOfType('voice.audio').map(m => m.data)).toEqual([
    'OK',
  ]);
});

test('her voice is played as it arrives and the state follows the brain', async () => {
  const { connection, audio, voice } = await open();
  connection.receive({
    type: 'voice.state',
    state: 'thinking',
    turn_id: 't-1',
  });
  expect(voice.state.phase).toBe('thinking');
  connection.receive({
    type: 'voice.state',
    state: 'speaking',
    turn_id: 't-1',
  });
  connection.receive({
    type: 'voice.audio',
    seq: 0,
    data: 'UENN',
    turn_id: 't-1',
  });
  connection.receive({
    type: 'voice.audio',
    seq: 1,
    data: '',
    turn_id: 't-1',
    last: true,
  });
  expect(audio.played).toEqual(['UENN']); // the empty "last" marker plays nothing
  connection.receive({ type: 'voice.state', state: 'listening' });
  expect(voice.state.phase).toBe('listening');
  expect(audio.log).not.toContain('flush'); // a natural end lets the buffered audio finish
});

test('a cut turn is dropped at once and its late audio is ignored', async () => {
  const { connection, audio, tts } = await open();
  connection.receive({
    type: 'voice.audio',
    seq: 0,
    data: 'UENN',
    turn_id: 't-1',
  });
  connection.receive({
    type: 'voice.state',
    state: 'listening',
    turn_id: 't-1',
  });
  expect(audio.log).toContain('flush');
  expect(tts.stops).toBeGreaterThan(0);
  connection.receive({
    type: 'voice.audio',
    seq: 1,
    data: 'LATE',
    turn_id: 't-1',
  });
  connection.receive({ type: 'voice.say', turn_id: 't-1', text: 'atrasada' });
  connection.receive({
    type: 'voice.audio',
    seq: 0,
    data: 'NEW',
    turn_id: 't-2',
  });
  expect(audio.played).toEqual(['UENN', 'NEW']);
  expect(tts.said).toEqual([]);
});

test('the transcript is the caption of what he said', async () => {
  const { connection, voice } = await open();
  connection.receive({
    type: 'voice.transcript',
    text: 'toca Djavan',
    final: true,
  });
  expect(voice.state.userText).toBe('toca Djavan');
});

test('the device voice speaks voice.say when the cloud voice is gone', async () => {
  const { connection, tts, voice } = await open('cloud', 'local');
  expect(voice.state.tts).toBe('local');
  connection.receive({
    type: 'voice.say',
    turn_id: 't-1',
    text: 'Oi, Petrus.',
  });
  connection.receive({
    type: 'voice.say',
    turn_id: 't-1',
    text: '',
    last: true,
  });
  expect(tts.said).toEqual(['Oi, Petrus.']);
});

test('the cloud STT running out hands over to the device recognizer and sends voice.text', async () => {
  const { connection, audio, stt, voice } = await open();
  connection.receive({
    type: 'voice.fallback',
    component: 'stt',
    mode: 'local',
    reason: 'quota',
  });
  expect(voice.state.stt).toBe('local');
  expect(stt.starts).toBe(1);
  audio.chunk?.('AAAA'); // no more audio to the brain
  expect(connection.sentOfType('voice.audio')).toEqual([]);
  stt.onText?.('  toca Oceano ');
  expect(connection.sentOfType('voice.text')).toEqual([
    { type: 'voice.text', id: 'v-2', text: 'toca Oceano' },
  ]);
  connection.receive({
    type: 'voice.fallback',
    component: 'stt',
    mode: 'cloud',
    reason: 'recovered',
  });
  expect(stt.stops).toBeGreaterThan(0);
  audio.chunk?.('BBBB');
  expect(connection.sentOfType('voice.audio').map(m => m.data)).toEqual([
    'BBBB',
  ]);
});

test('a session that opens with the STT already local uses the device recognizer', async () => {
  const { stt } = await open('local');
  expect(stt.starts).toBe(1);
});

test('the person ends it: the brain is told once and everything is released', async () => {
  const { connection, audio, tts, stt, voice } = await open();
  await voice.end();
  expect(connection.sentOfType('voice.end')).toEqual([
    { type: 'voice.end', reason: 'client' },
  ]);
  expect(audio.log).toEqual(expect.arrayContaining(['closePlayer', 'stop']));
  expect(tts.stops).toBeGreaterThan(0);
  expect(stt.stops).toBeGreaterThan(0);
  expect(voice.state).toMatchObject({ phase: 'off', notice: null });
  await voice.end(); // again: harmless
  expect(connection.sentOfType('voice.end')).toHaveLength(1);
});

test('the notification button ends the conversation', async () => {
  const { connection, audio, voice } = await open();
  audio.stopRequested?.();
  await new Promise(resolve => setImmediate(resolve));
  expect(connection.sentOfType('voice.end')).toHaveLength(1);
  expect(voice.state.phase).toBe('off');
});

test.each([
  ['command', 'Conversa de voz encerrada.'],
  ['silence', 'por silêncio'],
  ['server', 'O servidor encerrou'],
])(
  'the brain ending it (%s) is shown and sends nothing back',
  async (reason, text) => {
    const { connection, voice } = await open();
    connection.receive({ type: 'voice.end', reason });
    await new Promise(resolve => setImmediate(resolve));
    expect(voice.state.phase).toBe('off');
    expect(voice.state.notice).toContain(text);
    expect(connection.sentOfType('voice.end')).toEqual([]);
  },
);

test('without a connection it does not even open the microphone', async () => {
  const s = setup();
  s.connection.state = 'offline';
  expect(await s.voice.start()).toBe(false);
  expect(s.audio.log).toEqual([]);
  expect(s.voice.state.notice).toContain('Sem conexão');
});

test('no microphone permission ends cleanly with a notice', async () => {
  const s = setup();
  s.audio.failStart = true;
  expect(await s.voice.start()).toBe(false);
  expect(s.voice.state).toMatchObject({ phase: 'off' });
  expect(s.voice.state.notice).toContain('microfone');
  expect(s.connection.sent).toEqual([]);
});

test('voice_unavailable from the brain ends it with the brain message', async () => {
  const { connection, voice } = await open();
  connection.receive({
    type: 'error',
    code: 'voice_unavailable',
    message: 'A voz está desligada neste servidor.',
    retryable: false,
  });
  await new Promise(resolve => setImmediate(resolve));
  expect(voice.state.phase).toBe('off');
  expect(voice.state.notice).toBe('A voz está desligada neste servidor.');
});

test('an error that is not about the voice does not end the session', async () => {
  const { connection, voice } = await open();
  connection.receive({
    type: 'error',
    code: 'llm_unavailable',
    message: 'x',
    retryable: true,
  });
  expect(voice.state.phase).toBe('listening');
});

test('losing the connection ends the voice session', async () => {
  const { connection, voice } = await open();
  connection.state = 'offline';
  connection.stateListener?.('offline');
  await new Promise(resolve => setImmediate(resolve));
  expect(voice.state.phase).toBe('off');
  expect(voice.state.notice).toContain('conexão caiu');
});

test('interrupt flushes her voice and tells the brain', async () => {
  const { connection, audio, tts, voice } = await open();
  voice.interrupt();
  expect(connection.sentOfType('voice.interrupt')).toHaveLength(1);
  expect(audio.log).toContain('flush');
  expect(tts.stops).toBeGreaterThan(0);
});

test('a second start while active does nothing', async () => {
  const { voice, connection } = await open();
  expect(await voice.start()).toBe(false);
  expect(connection.sentOfType('voice.start')).toHaveLength(1);
});
