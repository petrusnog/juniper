/** The only messages that cross the bridge between the window and the main process (ADR-033). */
import type { ConnectionState } from '../core/connection/BrainConnection';
import type { ChatState } from '../core/chat/state';
import type { ActivityEntry } from '../core/executor/Executor';

export interface UiSnapshot {
  connection: ConnectionState;
  chat: ChatState;
  activity: ActivityEntry[];
  filesPaused: boolean;
  hasCredential: boolean;
  secureStorage: boolean;
}

export const CHANNELS = {
  snapshot: 'juniper:snapshot',
  send: 'juniper:send',
  cancel: 'juniper:cancel',
  newChat: 'juniper:new',
  resume: 'juniper:resume',
  login: 'juniper:login',
  logout: 'juniper:logout',
  pauseFiles: 'juniper:pause-files',
  event: 'juniper:event',
} as const;

export interface LoginInput {
  url: string;
  clientId: string;
  clientSecret: string;
}

/** What the renderer may call. No path, no file, no system access crosses this bridge. */
export interface JuniperBridge {
  snapshot(): Promise<UiSnapshot>;
  send(text: string): Promise<boolean>;
  cancel(): Promise<void>;
  newChat(): Promise<void>;
  resume(sessionId: string): Promise<void>;
  login(input: LoginInput): Promise<{ ok: boolean; error?: string }>;
  logout(): Promise<void>;
  pauseFiles(paused: boolean): Promise<void>;
  onEvent(callback: (snapshot: UiSnapshot) => void): () => void;
}
