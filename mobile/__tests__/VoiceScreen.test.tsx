import React from 'react';
import ReactTestRenderer from 'react-test-renderer';
import { Text } from 'react-native';
import { ChatController } from '../src/chat/ChatController';
import type { BrainConnection } from '../src/connection/BrainConnection';
import { VoiceScreen } from '../src/ui/VoiceScreen';
import { VoiceController } from '../src/voice/VoiceController';

class Conn {
  state = 'connected';
  sent: Record<string, any>[] = [];
  ls: ((m: any) => void)[] = [];
  get l() {
    return (m: any) => this.ls.forEach(f => f(m));
  }
  send(m: Record<string, any>) {
    this.sent.push(m);
    return true;
  }
  onMessage(l: (m: any) => void) {
    this.ls.push(l);
    return () => undefined;
  }
  onState() {
    return () => undefined;
  }
}
const audio = {
  start: jest.fn(async () => undefined),
  stop: jest.fn(async () => undefined),
  onChunk: () => () => undefined,
  onStopRequested: () => () => undefined,
  hasHeadset: jest.fn(async () => false),
  applyRoute: jest.fn(async () => undefined),
  onRouteChange: () => () => undefined,
  pauseCapture: jest.fn(async () => undefined),
  resumeCapture: jest.fn(async () => undefined),
  openPlayer: jest.fn(async () => undefined),
  play: jest.fn(),
  flush: jest.fn(),
  closePlayer: jest.fn(async () => undefined),
};
const tts = { available: false, speak: jest.fn(), stop: jest.fn() };
const stt = {
  available: false,
  start: jest.fn(async () => undefined),
  stop: jest.fn(async () => undefined),
};

function mount(connection: 'connected' | 'offline' = 'connected') {
  const onShortcutChange = jest.fn();
  const conn = new Conn();
  const voice = new VoiceController(
    conn as unknown as BrainConnection,
    audio,
    tts,
    stt,
    () => 'v-1',
  );
  const chat = new ChatController(
    conn as unknown as BrainConnection,
    () => 'c-1',
  );
  chat.start();
  let tree!: ReactTestRenderer.ReactTestRenderer;
  ReactTestRenderer.act(() => {
    tree = ReactTestRenderer.create(
      <VoiceScreen
        voice={voice}
        chat={chat}
        connection={connection}
        shortcut={false}
        shortcutNotice={null}
        onShortcutChange={onShortcutChange}
      />,
    );
  });
  const texts = () =>
    tree.root.findAllByType(Text).map(t => [t.props.children].flat().join(''));
  return { conn, voice, root: tree.root, texts, onShortcutChange };
}

test('the big button opens the conversation and the screen follows the brain', async () => {
  const { conn, root, texts } = mount();
  expect(texts()).toContain('Toque para conversar');
  await ReactTestRenderer.act(async () => {
    await root.findByProps({ testID: 'toggle' }).props.onPress();
  });
  expect(conn.sent[0]).toMatchObject({
    type: 'voice.start',
    local_stt: false,
    local_tts: false,
  });
  expect(texts()).toContain('Abrindo a conversa…');
  await ReactTestRenderer.act(async () => {
    conn.l({
      type: 'voice.ready',
      reply_to: 'v-1',
      input: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
      output: { encoding: 'pcm_s16le', sample_rate: 24000, channels: 1 },
      stt: 'cloud',
      tts: 'cloud',
    });
    await Promise.resolve();
  });
  expect(texts()).toContain('Ouvindo');
  ReactTestRenderer.act(() => {
    conn.l({ type: 'voice.transcript', text: 'toca Djavan', final: true });
    conn.l({ type: 'voice.state', state: 'thinking', turn_id: 't-1' });
  });
  expect(texts()).toEqual(expect.arrayContaining(['Pensando…', 'toca Djavan']));
  expect(root.findByProps({ testID: 'interrupt' })).toBeTruthy();
});

test('her caption is the text of the chat turn she is answering', async () => {
  const { conn, root, texts } = mount();
  await ReactTestRenderer.act(async () => {
    await root.findByProps({ testID: 'toggle' }).props.onPress();
  });
  ReactTestRenderer.act(() => {
    conn.l({
      type: 'chat.started',
      reply_to: 'x',
      turn_id: 't-1',
      session_id: 's',
    });
    conn.l({ type: 'chat.token', turn_id: 't-1', text: 'Tocando Oceano.' });
  });
  expect(texts()).toContain('Tocando Oceano.');
});

test('offline the button is disabled and the screen says why', () => {
  const { root, texts } = mount('offline');
  expect(root.findByProps({ testID: 'toggle' }).props.disabled).toBe(true);
  expect(texts()).toContain('Sem conexão com a Juniper.');
});

test('the fallback voices are shown while the session is on', async () => {
  const { conn, root, texts } = mount();
  await ReactTestRenderer.act(async () => {
    await root.findByProps({ testID: 'toggle' }).props.onPress();
    conn.l({
      type: 'voice.ready',
      reply_to: 'v-1',
      input: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
      output: { encoding: 'pcm_s16le', sample_rate: 24000, channels: 1 },
      stt: 'cloud',
      tts: 'local',
    });
    await Promise.resolve();
  });
  expect(texts().join(' ')).toContain('voz do aparelho');
});

const settle = () =>
  ReactTestRenderer.act(async () => {
    await new Promise(resolve => setImmediate(resolve));
  });

test('the screen asks for "display over other apps" when it is not granted', async () => {
  const { root, texts } = mount();
  await settle();
  expect(root.findByProps({ testID: 'overlay-banner' })).toBeTruthy();
  expect(texts().join(' ')).toContain('Exibir sobre outros apps');
  expect(root.findByProps({ testID: 'overlay-allow' })).toBeTruthy();
});

test('hold-to-talk shows the hold button only during a conversation, and it drives the microphone', async () => {
  const { conn, voice, root } = mount();
  await settle();
  expect(root.findAllByProps({ testID: 'talk' })).toHaveLength(0);
  await ReactTestRenderer.act(async () => {
    await root.findByProps({ testID: 'toggle' }).props.onPress();
    conn.l({
      type: 'voice.ready',
      reply_to: 'v-1',
      input: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
      output: { encoding: 'pcm_s16le', sample_rate: 24000, channels: 1 },
      stt: 'cloud',
      tts: 'cloud',
    });
    await Promise.resolve();
  });
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'ptt-switch' }).props.onValueChange(true),
  );
  const talk = root.findByProps({ testID: 'talk' });
  ReactTestRenderer.act(() => talk.props.onPressIn());
  expect(voice.state.talking).toBe(true);
  ReactTestRenderer.act(() => talk.props.onPressOut());
  expect(voice.state.talking).toBe(false);
});

test('the screen says whether she is on the loudspeaker (no interrupting by voice) or on headphones', async () => {
  const { conn, root, texts } = mount();
  await settle();
  await ReactTestRenderer.act(async () => {
    await root.findByProps({ testID: 'toggle' }).props.onPress();
    await Promise.resolve();
  });
  expect(texts().join(' ')).toContain('Viva-voz');
  expect(conn.sent[0]).toMatchObject({ route: 'speaker' });
});

test('the notification button switch asks the app to turn it on', () => {
  const { root, onShortcutChange } = mount();
  ReactTestRenderer.act(() => {
    root.findByProps({ testID: 'shortcut-switch' }).props.onValueChange(true);
  });
  expect(onShortcutChange).toHaveBeenCalledWith(true);
});
