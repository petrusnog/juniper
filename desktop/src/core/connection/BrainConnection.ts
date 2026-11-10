/**
 * The WebSocket to the brain (ADR-012, ADR-033 section 2).
 *
 * It opens the socket with `Authorization: Bearer client_id.client_secret` (ADR-027), sends
 * `hello` (kind `desktop`), answers `ping`, reconnects with back-off and tells the UI where it
 * stands (`state`). It knows nothing about chat or tools: it hands the other messages to
 * `onMessage`. Nothing here logs the credential. The socket is injected, so this file does not
 * import `ws` or Electron.
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
} from '../../shared/protocol';
import { delayFor } from './backoff';
import { describeClose, describeSocketError } from './diagnose';

export type ConnectionState =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'offline' // no network or the brain does not answer: retrying
  | 'unauthorized' // 4401: the credential was refused, retrying will not help
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

export type SocketFactory = (url: string, headers: Record<string, string>) => SocketLike;

export interface ConnectionOptions {
  url: string;
  /** `client_id.client_secret`: held in memory only for the handshake. */
  bearer: string;
  deviceId: string;
  deviceName?: string;
  appVersion: string;
  /** What this device offers beyond chat (`{ fs: true }` registers the file tools). */
  capabilities?: Record<string, boolean>;
  /** Conversation to resume on (re)connection: the one that was open. */
  resume?: () => { session_id?: string; latest?: boolean } | undefined;
  createSocket: SocketFactory;
  random?: () => number;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

const STABLE_AFTER_MS = 30_000;

type Listener<T> = (value: T) => void;

export class BrainConnection {
  state: ConnectionState = 'idle';
  /** The last reason a connection failed or closed (masked); `null` while all is well. */
  detail: string | null = null;
  private socket: SocketLike | null = null;
  private attempt = 0;
  private openedAt = 0;
  private retryTimer: unknown = null;
  private wanted = false;
  private readonly messageListeners = new Set<Listener<AnyMessage & { type: string }>>();
  private readonly stateListeners = new Set<Listener<ConnectionState>>();
  private readonly random: () => number;
  private readonly now: () => number;
  private readonly setTimer: (fn: () => void, ms: number) => unknown;
  private readonly clearTimer: (handle: unknown) => void;

  constructor(private readonly o: ConnectionOptions) {
    this.random = o.random ?? Math.random;
    this.now = o.now ?? Date.now;
    this.setTimer = o.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = o.clearTimer ?? (h => clearTimeout(h as ReturnType<typeof setTimeout>));
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
    if (state === 'connected') this.detail = null;
    this.stateListeners.forEach(l => l(state));
  }

  private open(): void {
    if (this.socket !== null) {
      return;
    }
    this.setState('connecting');
    let socket: SocketLike;
    try {
      socket = this.o.createSocket(this.o.url, { Authorization: `Bearer ${this.o.bearer}` });
    } catch (e) {
      this.detail = describeSocketError(e) || 'não consegui abrir o socket';
      this.scheduleRetry();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      this.openedAt = this.now();
      const resume = this.o.resume?.();
      socket.send(
        serialize({
          type: 'hello',
          protocol_version: PROTOCOL_VERSION,
          client: {
            kind: 'desktop',
            version: this.o.appVersion,
            device_id: this.o.deviceId,
            ...(this.o.deviceName ? { device_name: this.o.deviceName } : {}),
            ...(this.o.capabilities ? { capabilities: this.o.capabilities } : {}),
          },
          ...(resume ? { resume } : {}),
        }),
      );
    };
    socket.onmessage = event => this.handle(socket, event.data);
    socket.onerror = event => {
      this.detail = describeSocketError(event) || this.detail; // onclose follows and ends it
    };
    socket.onclose = event => this.closed(socket, event.code, event.reason);
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

  private closed(socket: SocketLike, code: number, reason?: string): void {
    if (this.socket !== socket) {
      return;
    }
    this.socket = null;
    if (this.detail === null) this.detail = describeClose(code, reason);
    if (code === CLOSE_UNAUTHORIZED) return this.stop('unauthorized');
    if (code === CLOSE_UNSUPPORTED_VERSION) return this.stop('incompatible');
    if (code === CLOSE_REPLACED) return this.stop('replaced');
    const stable = this.openedAt > 0 && this.now() - this.openedAt >= STABLE_AFTER_MS;
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
    const delay = fixedDelay ?? delayFor(this.attempt, this.random);
    this.attempt += 1;
    this.clearRetry();
    this.retryTimer = this.setTimer(() => {
      this.retryTimer = null;
      this.open();
    }, delay);
  }

  private clearRetry(): void {
    if (this.retryTimer !== null) {
      this.clearTimer(this.retryTimer);
      this.retryTimer = null;
    }
  }
}
