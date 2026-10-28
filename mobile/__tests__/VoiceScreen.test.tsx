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
      <VoiceScreen voice={voice} chat={chat} connection={connection} />,
    );
  });
  const texts = () =>
    tree.root.findAllByType(Text).map(t => [t.props.children].flat().join(''));
  return { conn, voice, root: tree.root, texts };
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
