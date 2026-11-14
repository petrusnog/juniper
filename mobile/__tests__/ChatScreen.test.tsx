import React from 'react';
import ReactTestRenderer from 'react-test-renderer';
import { Keyboard, Text } from 'react-native';
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
  // The window position of the screen: it ends at y = 700 (the keyboard is measured from it).
  // (The react-native jest mock of View ignores `createNodeMock`, so the instance is patched.)
  const frame = root
    .findAllByProps({ testID: 'chat-frame' })
    .find(node => node.instance);
  if (frame) {
    frame.instance.measureInWindow = (cb: (...n: number[]) => void) =>
      cb(0, 24, 360, 676);
  }
  const texts = () =>
    root.findAllByType(Text).map(t => [t.props.children].flat().join(''));
  return { fake, chat, root, texts };
}

function frameBottomPadding(root: ReactTestRenderer.ReactTestInstance) {
  const style = root.findByProps({ testID: 'chat-frame' }).props.style;
  return (style as { paddingBottom?: number }[]).find(
    s => s && s.paddingBottom !== undefined,
  )?.paddingBottom;
}

function keyboardEvent(height: number, screenY = 800 - height) {
  return { endCoordinates: { height, screenY, width: 360, screenX: 0 } };
}

test('the message box rises by exactly what the keyboard covers, whatever its size', () => {
  const listeners: Record<string, (e: unknown) => void> = {};
  jest.spyOn(Keyboard, 'addListener').mockImplementation(((
    name: string,
    cb: (e: unknown) => void,
  ) => {
    listeners[name] = cb;
    return { remove: jest.fn() };
  }) as never);
  const { root } = mount();
  expect(frameBottomPadding(root)).toBe(0);
  // The screen is 800 high and the screen of the app ends at 700: a 300 keyboard covers 200.
  ReactTestRenderer.act(() => listeners.keyboardDidShow(keyboardEvent(300)));
  expect(frameBottomPadding(root)).toBe(200);
  // The user's own, taller keyboard: more room is made.
  ReactTestRenderer.act(() =>
    listeners.keyboardDidChangeFrame(keyboardEvent(420)),
  );
  expect(frameBottomPadding(root)).toBe(320);
  // A keyboard that does not reach the box leaves it alone.
  ReactTestRenderer.act(() =>
    listeners.keyboardDidChangeFrame(keyboardEvent(60)),
  );
  expect(frameBottomPadding(root)).toBe(0);
  ReactTestRenderer.act(() => listeners.keyboardDidShow(keyboardEvent(300)));
  ReactTestRenderer.act(() => listeners.keyboardDidHide({}));
  expect(frameBottomPadding(root)).toBe(0);
  jest.restoreAllMocks();
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

test('the list shows titles and a conversation can be renamed from it', () => {
  const { fake, root } = mount();
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'conversations' }).props.onPress(),
  );
  ReactTestRenderer.act(() => {
    fake.listener?.({
      type: 'session.listed',
      reply_to: 'x',
      sessions: [
        {
          id: 's-7',
          preview: 'como faço um bolo de cenoura',
          turns: 3,
          updated_at: '2026-10-27T10:00:00Z',
          title: 'Receita de bolo',
          title_source: 'auto',
        },
      ],
    });
  });
  const shown = root
    .findByProps({ testID: 'session-s-7' })
    .findAllByType('Text' as any)
    .map(t => String(t.props.children))
    .join(' ');
  expect(shown).toContain('Receita de bolo');
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'rename-s-7' }).props.onPress(),
  );
  ReactTestRenderer.act(() =>
    root
      .findByProps({ testID: 'rename-input' })
      .props.onChangeText('Bolo da vó'),
  );
  ReactTestRenderer.act(() =>
    root.findByProps({ testID: 'rename-save' }).props.onPress(),
  );
  expect(fake.sent.at(-1)).toMatchObject({
    type: 'session.rename',
    title: 'Bolo da vó',
    session_id: 's-7',
  });
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
