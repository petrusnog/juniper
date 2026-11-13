import { describe, expect, test } from 'vitest';
import { BUSY_NOTICE, ChatController, OFFLINE_NOTICE } from '../src/core/chat/ChatController';

class FakeConnection {
  sent: Record<string, unknown>[] = [];
  up = true;
  private listener: ((m: { type: string }) => void) | null = null;
  onMessage(l: (m: { type: string }) => void) {
    this.listener = l;
    return () => undefined;
  }
  send(m: Record<string, unknown>) {
    if (!this.up) return false;
    this.sent.push(m);
    return true;
  }
  emit(m: Record<string, unknown>) {
    this.listener?.(m as { type: string });
  }
}

function setup() {
  const conn = new FakeConnection();
  let n = 0;
  const chat = new ChatController(conn as never, () => `id${(n += 1)}`);
  chat.start();
  return { conn, chat };
}

describe('ChatController (thin client)', () => {
  test('titles: the list, the open conversation and a rename follow what the brain says', () => {
    const { conn, chat } = setup();
    conn.emit({ type: 'welcome', protocol_version: 1, server_version: 'x', heartbeat_seconds: 15, session: { id: 's1', resumed: false, turns: 0 } });
    conn.emit({
      type: 'session.listed',
      reply_to: 'x',
      sessions: [
        { id: 's1', preview: 'oi', turns: 1, updated_at: '2026-10-27T00:00:00Z' },
        { id: 's2', preview: 'bolo', turns: 2, updated_at: '2026-10-27T00:00:00Z', title: 'Receita de bolo', title_source: 'auto' },
      ],
    });
    expect(chat.getState().sessions?.[1]?.title).toBe('Receita de bolo');
    conn.emit({ type: 'session.state', reply_to: 'y', session_id: 's1', turns: 1, history: [], note: '', title: '' });
    conn.emit({ type: 'session.titled', session_id: 's1', title: 'Conversa sobre jardinagem', source: 'auto' });
    expect(chat.getState().title).toBe('Conversa sobre jardinagem');
    expect(chat.getState().sessions?.[0]?.title).toBe('Conversa sobre jardinagem');
    expect(chat.rename('  Meu bolo  ', 's2')).toBe(true);
    expect(conn.sent.at(-1)).toMatchObject({ type: 'session.rename', title: 'Meu bolo', session_id: 's2' });
    conn.emit({ type: 'session.renamed', reply_to: 'z', session_id: 's2', title: 'Meu bolo' });
    expect(chat.getState().sessions?.[1]).toMatchObject({ title: 'Meu bolo', title_source: 'user' });
    expect(chat.getState().title).toBe('Conversa sobre jardinagem'); // another line: the open one is untouched
    const sent = conn.sent.length;
    expect(chat.rename('   ')).toBe(false);
    expect(conn.sent).toHaveLength(sent);
  });

  test('streams tokens into the assistant message', () => {
    const { conn, chat } = setup();
    expect(chat.send('oi')).toBe(true);
    conn.emit({ type: 'chat.started', session_id: 's1', turn_id: 't1' });
    conn.emit({ type: 'chat.token', turn_id: 't1', text: 'Olá' });
    conn.emit({ type: 'chat.token', turn_id: 't1', text: ', mundo' });
    conn.emit({ type: 'chat.end', turn_id: 't1', finish: 'ok' });
    expect(chat.getState().messages.map(m => [m.role, m.text])).toEqual([
      ['user', 'oi'],
      ['assistant', 'Olá, mundo'],
    ]);
    expect(chat.getState().activeTurn).toBeNull();
  });

  test('offline: the message is not sent and the notice says why', () => {
    const { conn, chat } = setup();
    conn.up = false;
    expect(chat.send('oi')).toBe(false);
    expect(chat.getState().messages.at(-1)?.text).toBe(OFFLINE_NOTICE);
  });

  test('busy: a second message waits', () => {
    const { conn, chat } = setup();
    chat.send('um');
    conn.emit({ type: 'chat.started', session_id: 's1', turn_id: 't1' });
    expect(chat.send('dois')).toBe(false);
    expect(chat.getState().messages.at(-1)?.text).toBe(BUSY_NOTICE);
  });

  test('a confirmation answer is a plain chat message (no buttons, ADR-010)', () => {
    const { conn, chat } = setup();
    chat.send('sim');
    expect(conn.sent.at(-1)).toMatchObject({ type: 'chat.send', text: 'sim' });
  });

  test('cancel sends chat.cancel for the active turn', () => {
    const { conn, chat } = setup();
    chat.send('oi');
    conn.emit({ type: 'chat.started', session_id: 's1', turn_id: 't1' });
    chat.cancel();
    expect(conn.sent.at(-1)).toEqual({ type: 'chat.cancel', turn_id: 't1' });
  });

  test('resumes the open conversation, else the latest', () => {
    const { conn, chat } = setup();
    expect(chat.resumeTarget()).toEqual({ latest: true });
    conn.emit({ type: 'welcome', session: { id: 's9', resumed: false, turns: 0 } });
    expect(chat.resumeTarget()).toEqual({ session_id: 's9' });
  });
});
