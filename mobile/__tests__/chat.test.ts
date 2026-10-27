import {
  BUSY_NOTICE,
  ChatController,
  OFFLINE_NOTICE,
} from '../src/chat/ChatController';
import { chatReducer, initialChat, isBusy } from '../src/chat/state';
import type { BrainConnection } from '../src/connection/BrainConnection';

type Sent = Record<string, unknown>;

class FakeConnection {
  sent: Sent[] = [];
  up = true;
  private listener: ((m: any) => void) | null = null;
  send(message: Sent) {
    if (!this.up) {
      return false;
    }
    this.sent.push(message);
    return true;
  }
  onMessage(l: (m: any) => void) {
    this.listener = l;
    return () => {
      this.listener = null;
    };
  }
  receive(message: Sent) {
    this.listener?.(message);
  }
}

function setup() {
  const connection = new FakeConnection();
  let n = 0;
  const controller = new ChatController(
    connection as unknown as BrainConnection,
    () => `id-${(n += 1)}`,
  );
  controller.start();
  return { connection, controller };
}

const WELCOME = (resumed = false, id = 's-1') => ({
  type: 'welcome',
  protocol_version: 1,
  server_version: 'x',
  heartbeat_seconds: 15,
  session: { id, resumed, turns: 0 },
});

test('a message streams in token by token and the turn ends', () => {
  const { connection, controller } = setup();
  connection.receive(WELCOME());
  expect(controller.send('  oi Juniper ')).toBe(true);
  expect(connection.sent.at(-1)).toEqual({
    type: 'chat.send',
    id: 'id-2',
    text: 'oi Juniper',
  });
  connection.receive({
    type: 'chat.started',
    reply_to: 'id-2',
    turn_id: 't-1',
    session_id: 's-1',
  });
  connection.receive({ type: 'chat.token', turn_id: 't-1', text: 'Olá, ' });
  connection.receive({ type: 'chat.token', turn_id: 't-1', text: 'Petrus.' });
  const mid = controller.state.messages.at(-1)!;
  expect(mid).toMatchObject({
    role: 'assistant',
    text: 'Olá, Petrus.',
    streaming: true,
  });
  expect(isBusy(controller.state)).toBe(true);
  connection.receive({ type: 'chat.end', turn_id: 't-1', finish: 'ok' });
  expect(controller.state.messages.map(m => [m.role, m.text])).toEqual([
    ['user', 'oi Juniper'],
    ['assistant', 'Olá, Petrus.'],
  ]);
  expect(isBusy(controller.state)).toBe(false);
});

test('while the brain is answering a second message is refused with a notice', () => {
  const { connection, controller } = setup();
  connection.receive(WELCOME());
  controller.send('um');
  expect(controller.send('dois')).toBe(false);
  expect(controller.state.messages.at(-1)).toMatchObject({
    role: 'notice',
    text: BUSY_NOTICE,
  });
  expect(connection.sent.filter(m => m.type === 'chat.send')).toHaveLength(1);
});

test('offline: nothing is sent, nothing is queued, the user is told and keeps the text', () => {
  const { connection, controller } = setup();
  connection.up = false;
  expect(controller.send('oi')).toBe(false);
  expect(controller.state.messages).toEqual([
    expect.objectContaining({ role: 'notice', text: OFFLINE_NOTICE }),
  ]);
  expect(isBusy(controller.state)).toBe(false);
});

test('empty or blank text is not sent', () => {
  const { connection, controller } = setup();
  expect(controller.send('   ')).toBe(false);
  expect(connection.sent).toEqual([]);
});

test('cancel asks the brain to stop the active turn, and a cancelled empty answer disappears', () => {
  const { connection, controller } = setup();
  controller.send('conta uma história longa');
  connection.receive({
    type: 'chat.started',
    reply_to: 'id-1',
    turn_id: 't-1',
    session_id: 's-1',
  });
  controller.cancel();
  expect(connection.sent.at(-1)).toEqual({
    type: 'chat.cancel',
    turn_id: 't-1',
  });
  connection.receive({ type: 'chat.end', turn_id: 't-1', finish: 'cancelled' });
  expect(controller.state.messages.map(m => m.role)).toEqual(['user']);
  expect(isBusy(controller.state)).toBe(false);
});

test('a cancelled answer that already had text keeps it, marked as cancelled', () => {
  const { connection, controller } = setup();
  controller.send('oi');
  connection.receive({
    type: 'chat.started',
    reply_to: 'id-1',
    turn_id: 't-1',
    session_id: 's-1',
  });
  connection.receive({
    type: 'chat.token',
    turn_id: 't-1',
    text: 'Era uma vez',
  });
  connection.receive({ type: 'chat.end', turn_id: 't-1', finish: 'cancelled' });
  expect(controller.state.messages.at(-1)).toMatchObject({
    text: 'Era uma vez',
    cancelled: true,
  });
});

test('cancel with no turn in progress sends nothing', () => {
  const { connection, controller } = setup();
  controller.cancel();
  expect(connection.sent).toEqual([]);
});

test('a typed error from the brain shows in the conversation and frees the chat', () => {
  const { connection, controller } = setup();
  controller.send('oi');
  connection.receive({
    type: 'error',
    code: 'llm_unavailable',
    message: 'Não consegui falar com nenhum LLM agora.',
    reply_to: 'id-1',
    retryable: true,
  });
  expect(controller.state.messages.at(-1)).toMatchObject({
    role: 'error',
    text: 'Não consegui falar com nenhum LLM agora.',
  });
  expect(isBusy(controller.state)).toBe(false);
});

test('an error in the middle of an answer ends with the turn, not with a stuck chat', () => {
  const { connection, controller } = setup();
  controller.send('oi');
  connection.receive({
    type: 'chat.started',
    reply_to: 'id-1',
    turn_id: 't-1',
    session_id: 's-1',
  });
  connection.receive({
    type: 'error',
    code: 'llm_timeout',
    message: 'Demorou.',
    reply_to: 'id-1',
    retryable: true,
  });
  connection.receive({ type: 'chat.end', turn_id: 't-1', finish: 'error' });
  expect(isBusy(controller.state)).toBe(false);
  expect(controller.state.messages.map(m => m.role)).toEqual(['user', 'error']);
});

test('a resumed welcome asks for the history of the conversation and lists the conversations', () => {
  const { connection } = setup();
  connection.receive(WELCOME(true, 's-9'));
  expect(connection.sent).toEqual([
    { type: 'session.resume', id: 'id-1', session_id: 's-9' },
    { type: 'session.list', id: 'id-2', limit: 50 },
  ]);
});

test('session.state replaces the messages with the history and shows the brain note', () => {
  const { connection, controller } = setup();
  connection.receive({
    type: 'session.state',
    reply_to: 'id-1',
    session_id: 's-9',
    turns: 2,
    note: 'Retomando a conversa de ontem.',
    history: [
      { role: 'user', text: 'oi' },
      { role: 'assistant', text: 'olá!' },
    ],
  });
  expect(controller.state.sessionId).toBe('s-9');
  expect(controller.state.messages.map(m => [m.role, m.text])).toEqual([
    ['user', 'oi'],
    ['assistant', 'olá!'],
    ['notice', 'Retomando a conversa de ontem.'],
  ]);
});

test('new conversation resets the screen only when the brain was reachable', () => {
  const { connection, controller } = setup();
  connection.receive(WELCOME());
  controller.send('oi');
  connection.receive({ type: 'chat.end', turn_id: 'x', finish: 'ok' });
  connection.up = false;
  controller.newConversation();
  expect(controller.state.messages).toHaveLength(1);
  connection.up = true;
  controller.newConversation();
  expect(controller.state.messages).toEqual([]);
  expect(connection.sent.map(m => m.type)).toContain('session.new');
});

test('resume asks the brain for that conversation', () => {
  const { connection, controller } = setup();
  controller.resume('s-3');
  expect(connection.sent.at(-1)).toMatchObject({
    type: 'session.resume',
    session_id: 's-3',
  });
});

test('the conversation list is mirrored', () => {
  const { connection, controller } = setup();
  connection.receive({
    type: 'session.listed',
    reply_to: 'id-2',
    sessions: [
      {
        id: 's-1',
        preview: 'oi',
        turns: 1,
        updated_at: '2026-10-27T00:00:00Z',
      },
    ],
  });
  expect(controller.state.sessions?.[0].id).toBe('s-1');
});

test('detached: another device took the conversation and the app says so', () => {
  const { connection, controller } = setup();
  connection.receive(WELCOME());
  connection.receive({
    type: 'session.detached',
    session_id: 's-1',
    reason: 'taken_by_other_connection',
  });
  expect(controller.state.sessionId).toBeNull();
  expect(controller.state.messages.at(-1)?.role).toBe('notice');
});

test('resumeTarget: the open conversation, or the last one on a first connection', () => {
  const { connection, controller } = setup();
  expect(controller.resumeTarget()).toEqual({ latest: true });
  connection.receive(WELCOME(false, 's-4'));
  expect(controller.resumeTarget()).toEqual({ session_id: 's-4' });
});

test('the confirmation "sim" is a plain message: the app has no confirmation state', () => {
  const { connection, controller } = setup();
  controller.send('apaga o evento');
  connection.receive({
    type: 'chat.started',
    reply_to: 'id-1',
    turn_id: 't-1',
    session_id: 's-1',
  });
  connection.receive({
    type: 'chat.token',
    turn_id: 't-1',
    text: 'Posso apagar o evento?',
  });
  connection.receive({ type: 'chat.end', turn_id: 't-1', finish: 'ok' });
  expect(controller.send('sim')).toBe(true);
  expect(connection.sent.at(-1)).toEqual({
    type: 'chat.send',
    id: 'id-2',
    text: 'sim',
  });
  expect(Object.keys(controller.state).sort()).toEqual([
    'activeTurn',
    'messages',
    'pendingSend',
    'sessionId',
    'sessions',
  ]);
});

test('the reducer ignores messages it does not know', () => {
  expect(
    chatReducer(initialChat, {
      kind: 'server',
      message: { type: 'tools.listed' } as any,
    }),
  ).toBe(initialChat);
});
