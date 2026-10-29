import React from 'react';
import ReactTestRenderer from 'react-test-renderer';
import { KeyboardAvoidingView, Text } from 'react-native';
import { ChatController } from '../src/chat/ChatController';
import type { BrainConnection } from '../src/connection/BrainConnection';
import { ChatScreen } from '../src/ui/ChatScreen';

class Fake {
  sent: Record<string, unknown>[] = [];
  up = true;
  listener: ((m: unknown) => void) | null = null;
  send(m: Record<string, unknown>) {
    if (!this.up) {
      return false;
    }
    this.sent.push(m);
    return true;
  }
  onMessage(l: (m: unknown) => void) {
    this.listener = l;
    return () => undefined;
  }
}

function mount(connection: 'connected' | 'offline' = 'connected') {
  const fake = new Fake();
  let n = 0;
  const chat = new ChatController(
    fake as unknown as BrainConnection,
    () => `id-${(n += 1)}`,
  );
  chat.start();
  let tree!: ReactTestRenderer.ReactTestRenderer;
  ReactTestRenderer.act(() => {
    tree = ReactTestRenderer.create(
      <ChatScreen
        chat={chat}
        connection={connection}
        onReconnect={jest.fn()}
        onConfigure={jest.fn()}
      />,
    );
  });
  const root = tree.root;
  const texts = () =>
    root.findAllByType(Text).map(t => [t.props.children].flat().join(''));
  return { fake, chat, root, texts };
}

test('the screen lifts the message box above the keyboard on Android too', () => {
  // With edge-to-edge (Android 15+) `adjustResize` does nothing: the screen has to make room.
  const { root } = mount();
  expect(root.findByType(KeyboardAvoidingView).props.behavior).toBe('padding');
});

test('typing and sending shows the message and clears the box', () => {
  const { fake, root, texts } = mount();
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'input' }).props.onChangeText('oi Juniper'),
  );
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'send' }).props.onPress(),
  );
  expect(fake.sent.at(-1)).toMatchObject({
    type: 'chat.send',
    text: 'oi Juniper',
  });
  expect(texts()).toContain('oi Juniper');
  expect(root.findByProps({ testID: 'input' }).props.value).toBe('');
});

test('while she answers the button becomes "Parar" and cancels the turn', () => {
  const { fake, chat, root } = mount();
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'input' }).props.onChangeText('oi'),
  );
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'send' }).props.onPress(),
  );
  ReactTestRenderer.act(() => {
    fake.listener?.({
      type: 'chat.started',
      reply_to: 'id-1',
      turn_id: 't-1',
      session_id: 's',
    });
    fake.listener?.({ type: 'chat.token', turn_id: 't-1', text: 'Olá' });
  });
  expect(chat.state.activeTurn).toBe('t-1');
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'cancel' }).props.onPress(),
  );
  expect(fake.sent.at(-1)).toEqual({ type: 'chat.cancel', turn_id: 't-1' });
});

test('offline: the text stays in the box and the user is told', () => {
  const { fake, root, texts } = mount('offline');
  fake.up = false;
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'input' }).props.onChangeText('não perca isto'),
  );
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'send' }).props.onPress(),
  );
  expect(root.findByProps({ testID: 'input' }).props.value).toBe(
    'não perca isto',
  );
  expect(texts().some(t => t.includes('Sem conexão com a Juniper'))).toBe(true);
  expect(texts().some(t => t.includes('Sem conexão com o servidor'))).toBe(
    true,
  );
});

test('the conversation list opens and resumes the one that was tapped', () => {
  const { fake, root } = mount();
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'conversations' }).props.onPress(),
  );
  expect(fake.sent.at(-1)).toMatchObject({ type: 'session.list' });
  ReactTestRenderer.act(() => {
    fake.listener?.({
      type: 'session.listed',
      reply_to: 'x',
      sessions: [
        {
          id: 's-7',
          preview: 'receita de bolo',
          turns: 3,
          updated_at: '2026-10-27T10:00:00Z',
        },
      ],
    });
  });
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'session-s-7' }).props.onPress(),
  );
  expect(fake.sent.at(-1)).toMatchObject({
    type: 'session.resume',
    session_id: 's-7',
  });
});
