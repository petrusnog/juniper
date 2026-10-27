/**
 * The text chat on top of a `BrainConnection`: sends, cancels, lists and resumes conversations
 * and keeps `ChatState` up to date. Plain TypeScript (no React) so it is easy to test.
 */
import type { BrainConnection } from '../connection/BrainConnection';
import { isType } from '../protocol';
import {
  ChatAction,
  chatReducer,
  ChatState,
  initialChat,
  isBusy,
} from './state';

export const OFFLINE_NOTICE =
  'Sem conexão com a Juniper agora. Sua mensagem não foi enviada: confira o Tailscale e tente de novo.';
export const BUSY_NOTICE =
  'A Juniper ainda está respondendo. Espere ou cancele a resposta.';

type Listener = () => void;

export class ChatController {
  state: ChatState = initialChat;
  private readonly listeners = new Set<Listener>();
  private counter = 0;
  private off: (() => void) | null = null;

  constructor(
    private readonly connection: BrainConnection,
    private readonly newId: () => string = () =>
      `m-${Date.now().toString(36)}-${(this.counter += 1)}`,
  ) {}

  start(): void {
    this.off = this.connection.onMessage(message => {
      this.dispatch({ kind: 'server', message });
      if (isType(message, 'welcome')) {
        if (message.session.resumed) {
          // The welcome carries no history: ask for the conversation we are in.
          this.send_({
            type: 'session.resume',
            id: this.newId(),
            session_id: message.session.id,
          });
        }
        this.refreshSessions();
      }
    });
  }

  stop(): void {
    this.off?.();
    this.off = null;
  }

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getState = (): ChatState => this.state;

  /** What to resume when the connection (re)opens: the open conversation, else the last one. */
  resumeTarget = (): { session_id?: string; latest?: boolean } =>
    this.state.sessionId
      ? { session_id: this.state.sessionId }
      : { latest: true };

  /** Sends a message; `false` (and a notice) if it could not go, so the UI keeps the text. */
  send(text: string): boolean {
    const clean = text.trim();
    if (clean === '') {
      return false;
    }
    if (isBusy(this.state)) {
      this.dispatch({ kind: 'notice', text: BUSY_NOTICE });
      return false;
    }
    const id = this.newId();
    if (!this.send_({ type: 'chat.send', id, text: clean })) {
      this.dispatch({ kind: 'notice', text: OFFLINE_NOTICE });
      return false;
    }
    this.dispatch({ kind: 'sent', id, text: clean });
    return true;
  }

  cancel(): void {
    const turn = this.state.activeTurn;
    if (turn !== null) {
      this.send_({ type: 'chat.cancel', turn_id: turn });
    }
  }

  newConversation(): void {
    if (this.send_({ type: 'session.new', id: this.newId() })) {
      this.dispatch({ kind: 'reset' });
      this.refreshSessions();
    }
  }

  resume(sessionId: string): void {
    this.send_({
      type: 'session.resume',
      id: this.newId(),
      session_id: sessionId,
    });
  }

  refreshSessions(): void {
    this.send_({ type: 'session.list', id: this.newId(), limit: 50 });
  }

  private send_(message: Parameters<BrainConnection['send']>[0]): boolean {
    return this.connection.send(message);
  }

  private dispatch(action: ChatAction): void {
    this.state = chatReducer(this.state, action);
    this.listeners.forEach(l => l());
  }
}
