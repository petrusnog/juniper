import type { ConnectionState } from '../src/connection/BrainConnection';
import { isLaunchUrl, VoiceLauncher } from '../src/launch/VoiceLauncher';

class Conn {
  state: ConnectionState;
  listeners: ((s: ConnectionState) => void)[] = [];
  constructor(state: ConnectionState = 'connected') {
    this.state = state;
  }
  onState(l: (s: ConnectionState) => void) {
    this.listeners.push(l);
    return () => {
      this.listeners = this.listeners.filter(x => x !== l);
    };
  }
  become(state: ConnectionState) {
    this.state = state;
    this.listeners.forEach(l => l(state));
  }
}

function voiceStub(phase = 'off') {
  return {
    state: { phase },
    start: jest.fn(async (_options?: { wake?: boolean }) => true),
  };
}

test('only the juniper://voz link counts as the voice link', () => {
  expect(isLaunchUrl('juniper://voz')).toBe(true);
  expect(isLaunchUrl(' JUNIPER://VOZ ')).toBe(true);
  expect(isLaunchUrl('juniper://outra')).toBe(false);
  expect(isLaunchUrl('https://juniper.example/voz')).toBe(false);
  expect(isLaunchUrl(null)).toBe(false);
  expect(isLaunchUrl(undefined)).toBe(false);
});

test('connected: opens the voice session in hands-free mode right away', async () => {
  const voice = voiceStub();
  const launcher = new VoiceLauncher(new Conn() as any, voice as any, 50);
  expect(await launcher.trigger()).toBe(true);
  expect(voice.start).toHaveBeenCalledWith({ wake: true });
});

test('a conversation already on is left alone', async () => {
  const voice = voiceStub('listening');
  const launcher = new VoiceLauncher(new Conn() as any, voice as any, 50);
  expect(await launcher.trigger()).toBe(false);
  expect(voice.start).not.toHaveBeenCalled();
});

test('a cold start waits for the connection and then opens the session', async () => {
  const conn = new Conn('connecting');
  const voice = voiceStub();
  const launcher = new VoiceLauncher(conn as any, voice as any, 1000);
  const pending = launcher.trigger();
  await Promise.resolve();
  expect(voice.start).not.toHaveBeenCalled();
  conn.become('connected');
  expect(await pending).toBe(true);
  expect(voice.start).toHaveBeenCalledWith({ wake: true });
  expect(conn.listeners).toHaveLength(0); // it stopped listening to the connection
});

test('still offline after the wait: the voice start explains it, nothing hangs', async () => {
  const conn = new Conn('offline');
  const voice = voiceStub();
  voice.start.mockResolvedValue(false);
  const launcher = new VoiceLauncher(conn as any, voice as any, 20);
  expect(await launcher.trigger()).toBe(false);
  expect(voice.start).toHaveBeenCalledTimes(1);
  expect(conn.listeners).toHaveLength(0);
});
