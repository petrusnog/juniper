/**
 * The state of the text chat as a pure reducer (ADR-022, section 4). The brain owns the
 * conversation; this only mirrors what it sent: messages, the turn in progress and the list of
 * conversations. No agent logic and no confirmation state live here (ADR-010).
 */
import type { AnyMessage } from '../protocol';
import { isType } from '../protocol';

export interface ChatMessage {
  key: string;
  role: 'user' | 'assistant' | 'notice' | 'error';
  text: string;
  turnId?: string;
  streaming?: boolean;
  cancelled?: boolean;
}

export interface SessionInfo {
  id: string;
  preview: string;
  turns: number;
  updated_at: string;
}

export interface ChatState {
  sessionId: string | null;
  messages: ChatMessage[];
  /** The turn the brain is answering now (null when idle). */
  activeTurn: string | null;
  /** A chat.send waiting for its chat.started (the id the app generated). */
  pendingSend: string | null;
  sessions: SessionInfo[] | null;
}

export const initialChat: ChatState = {
  sessionId: null,
  messages: [],
  activeTurn: null,
  pendingSend: null,
  sessions: null,
};

export type ChatAction =
  | { kind: 'server'; message: AnyMessage & { type: string } }
  | { kind: 'sent'; id: string; text: string }
  | { kind: 'notice'; text: string }
  | { kind: 'reset' };

function patchTurn(
  messages: ChatMessage[],
  turnId: string,
  patch: (m: ChatMessage) => ChatMessage,
): ChatMessage[] {
  return messages.map(m =>
    m.turnId === turnId && m.role === 'assistant' ? patch(m) : m,
  );
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  if (action.kind === 'reset') {
    return { ...initialChat, sessions: state.sessions };
  }
  if (action.kind === 'sent') {
    return {
      ...state,
      pendingSend: action.id,
      messages: [
        ...state.messages,
        { key: `u-${action.id}`, role: 'user', text: action.text },
      ],
    };
  }
  if (action.kind === 'notice') {
    return {
      ...state,
      messages: [
        ...state.messages,
        {
          key: `n-${state.messages.length}`,
          role: 'notice',
          text: action.text,
        },
      ],
    };
  }
  const message = action.message;
  if (isType(message, 'welcome')) {
    return {
      ...state,
      sessionId: message.session.id,
      activeTurn: null,
      pendingSend: null,
    };
  }
  if (isType(message, 'chat.started')) {
    return {
      ...state,
      sessionId: message.session_id,
      activeTurn: message.turn_id,
      pendingSend: null,
      messages: [
        ...state.messages,
        {
          key: `a-${message.turn_id}`,
          role: 'assistant',
          text: '',
          turnId: message.turn_id,
          streaming: true,
        },
      ],
    };
  }
  if (isType(message, 'chat.token')) {
    return {
      ...state,
      messages: patchTurn(state.messages, message.turn_id, m => ({
        ...m,
        text: m.text + message.text,
      })),
    };
  }
  if (isType(message, 'chat.end')) {
    const finish = message.finish;
    return {
      ...state,
      activeTurn:
        state.activeTurn === message.turn_id ? null : state.activeTurn,
      messages: patchTurn(state.messages, message.turn_id, m => ({
        ...m,
        streaming: false,
        cancelled: finish === 'cancelled',
      })).filter(
        m =>
          !(m.turnId === message.turn_id && m.text === '' && finish !== 'ok'),
      ),
    };
  }
  if (isType(message, 'error')) {
    const ownTurn =
      message.reply_to !== undefined && message.reply_to === state.pendingSend;
    return {
      ...state,
      pendingSend: ownTurn ? null : state.pendingSend,
      messages: [
        ...state.messages,
        {
          key: `e-${state.messages.length}`,
          role: 'error',
          text: message.message,
        },
      ],
    };
  }
  if (isType(message, 'session.state')) {
    const history: ChatMessage[] = (message.history ?? []).map((item, i) => ({
      key: `h-${message.session_id}-${i}`,
      role: item.role === 'user' ? 'user' : 'assistant',
      text: item.text,
    }));
    if (message.note) {
      history.push({
        key: `note-${message.session_id}`,
        role: 'notice',
        text: message.note,
      });
    }
    return {
      ...state,
      sessionId: message.session_id,
      activeTurn: null,
      pendingSend: null,
      messages: history,
    };
  }
  if (isType(message, 'session.listed')) {
    return { ...state, sessions: message.sessions };
  }
  if (isType(message, 'session.detached')) {
    return {
      ...state,
      sessionId: null,
      activeTurn: null,
      pendingSend: null,
      messages: [
        ...state.messages,
        {
          key: `n-${state.messages.length}`,
          role: 'notice',
          text: 'Esta conversa foi aberta em outro aparelho. Abra uma conversa para continuar.',
        },
      ],
    };
  }
  return state;
}

export function isBusy(state: ChatState): boolean {
  return state.activeTurn !== null || state.pendingSend !== null;
}
