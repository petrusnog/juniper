/**
 * The WebSocket to the brain (ADR-012, ADR-022 section 3).
 *
 * It opens the socket with the bearer token, sends `hello`, answers `ping`, reconnects with
 * back-off and tells the UI where it stands (`state`). It knows nothing about chat or voice: it
 * hands the other messages to `onMessage`. Nothing here logs the token.
 */
import {
  AnyMessage,
  CLOSE_REPLACED,
  CLOSE_SERVICE_RESTART,
  CLOSE_UNAUTHORIZED,
  CLOSE_UNSUPPORTED_VERSION,
  isType,
  parseMessage,
  PROTOCOL_VERSION,
  serialize,
} from '../protocol';
import { delayFor } from './backoff';

export type ConnectionState =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'offline' // no network or the brain does not answer: retrying
  | 'unauthorized' // 4401: the token was refused, retrying will not help
  | 'incompatible' // 4426: the app and the brain speak different protocol versions
  | 'replaced'; // 4409: another connection of this device took over

export interface SocketLike {
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number; reason?: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

export type SocketFactory = (
  url: string,
  headers: Record<string, string>,
) => SocketLike;

export interface ConnectionOptions {
  url: string;
  token: string;
  deviceId: string;
  deviceName?: string;
  appVersion: string;
  /** Conversation to resume on (re)connection: the one that was open. */
  resume?: () => { session_id?: string; latest?: boolean } | undefined;
  createSocket?: SocketFactory;
  random?: () => number;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

const STABLE_AFTER_MS = 30_000;

const defaultSocket: SocketFactory = (url, headers) =>
  // React Native accepts headers in the third argument of WebSocket.
  new (WebSocket as unknown as new (
    u: string,
    p: null,
    o: { headers: Record<string, string> },
  ) => SocketLike)(url, null, { headers });

type Listener<T> = (value: T) => void;

export class BrainConnection {
  state: ConnectionState = 'idle';
  private socket: SocketLike | null = null;
  private attempt = 0;
  private openedAt = 0;
  private retryTimer: unknown = null;
  private wanted = false;
  private readonly messageListeners = new Set<
    Listener<AnyMessage & { type: string }>
  >();
  private readonly stateListeners = new Set<Listener<ConnectionState>>();
  private readonly o: Required<
    Pick<
      ConnectionOptions,
      'createSocket' | 'random' | 'now' | 'setTimer' | 'clearTimer'
    >
  > &
    ConnectionOptions;

  constructor(options: ConnectionOptions) {
    this.o = {
      createSocket: defaultSocket,
      random: Math.random,
      now: Date.now,
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: handle =>
        clearTimeout(handle as ReturnType<typeof setTimeout>),
      ...options,
    };
  }

  onMessage(listener: Listener<AnyMessage & { type: string }>): () => void {
    this.messageListeners.add(listener);
    return () => this.messageListeners.delete(listener);
  }

  onState(listener: Listener<ConnectionState>): () => void {
    this.stateListeners.add(listener);
    return () => this.stateListeners.delete(listener);
  }

  connect(): void {
    this.wanted = true;
    this.open();
  }

  /** Sends a message; `false` if the connection is not up (nothing is queued: ADR-012). */
  send(message: AnyMessage & { type: string }): boolean {
    if (this.state !== 'connected' || this.socket === null) {
      return false;
    }
    try {
      this.socket.send(serialize(message));
      return true;
    } catch {
      return false;
    }
  }

  close(): void {
    this.wanted = false;
    this.clearRetry();
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onclose = null;
      socket.close(1000, 'client closing');
    }
    this.setState('idle');
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) {
      return;
    }
    this.state = state;
    this.stateListeners.forEach(l => l(state));
  }

  private open(): void {
    if (this.socket !== null) {
      return;
    }
    this.setState('connecting');
    let socket: SocketLike;
    try {
      socket = this.o.createSocket(this.o.url, {
        Authorization: `Bearer ${this.o.token}`,
      });
    } catch {
      this.scheduleRetry();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      this.openedAt = this.o.now();
      const resume = this.o.resume?.();
      socket.send(
        serialize({
          type: 'hello',
          protocol_version: PROTOCOL_VERSION,
          client: {
            kind: 'mobile',
            version: this.o.appVersion,
            device_id: this.o.deviceId,
            ...(this.o.deviceName ? { device_name: this.o.deviceName } : {}),
          },
          ...(resume ? { resume } : {}),
        }),
      );
    };
    socket.onmessage = event => this.handle(socket, event.data);
    socket.onerror = () => undefined; // onclose follows and carries the outcome
    socket.onclose = event => this.closed(socket, event.code);
  }

  private handle(socket: SocketLike, data: unknown): void {
    if (typeof data !== 'string') {
      return; // the brain never sends binary frames (ADR-012)
    }
    let message: AnyMessage & { type: string };
    try {
      message = parseMessage(data);
    } catch {
      return; // one bad frame is not a reason to drop the connection
    }
    if (isType(message, 'ping')) {
      socket.send(serialize({ type: 'pong' }));
      return;
    }
    if (isType(message, 'welcome')) {
      this.setState('connected');
    }
    this.messageListeners.forEach(l => l(message));
  }

  private closed(socket: SocketLike, code: number): void {
    if (this.socket !== socket) {
      return;
    }
    this.socket = null;
    if (code === CLOSE_UNAUTHORIZED) {
      return this.stop('unauthorized');
    }
    if (code === CLOSE_UNSUPPORTED_VERSION) {
      return this.stop('incompatible');
    }
    if (code === CLOSE_REPLACED) {
      return this.stop('replaced');
    }
    const stable =
      this.openedAt > 0 && this.o.now() - this.openedAt >= STABLE_AFTER_MS;
    if (stable) {
      this.attempt = 0;
    }
    this.openedAt = 0;
    if (code === CLOSE_SERVICE_RESTART) {
      this.attempt = 0; // a deploy: the brain is back in seconds
    }
    this.scheduleRetry(code === CLOSE_SERVICE_RESTART ? 500 : undefined);
  }

  private stop(state: ConnectionState): void {
    this.wanted = false;
    this.setState(state);
  }

  private scheduleRetry(fixedDelay?: number): void {
    if (!this.wanted) {
      return;
    }
    this.setState('offline');
    const delay = fixedDelay ?? delayFor(this.attempt, this.o.random);
    this.attempt += 1;
    this.clearRetry();
    this.retryTimer = this.o.setTimer(() => {
      this.retryTimer = null;
      this.open();
    }, delay);
  }

  private clearRetry(): void {
    if (this.retryTimer !== null) {
      this.o.clearTimer(this.retryTimer);
      this.retryTimer = null;
    }
  }
}
