/** The only messages that cross the bridge between the window and the main process (ADR-033). */
import type { ConnectionState } from '../core/connection/BrainConnection';
import type { ChatState } from '../core/chat/state';
import type { ActivityEntry } from '../core/executor/Executor';
import type { UpdateState } from '../core/update/UpdateController';
import type { VoiceState } from '../core/voice/VoiceController';
import type { AudioCommand, AudioEvent } from './audioProtocol';

export interface UiSnapshot {
  connection: ConnectionState;
  /** Why the connection is not up (masked), with a hint on what to try. */
  connectionDetail: string | null;
  connectionHint: string | null;
  chat: ChatState;
  activity: ActivityEntry[];
  filesPaused: boolean;
  hasCredential: boolean;
  secureStorage: boolean;
  voice: VoiceState;
  update: UpdateState;
  version: string;
}

export const CHANNELS = {
  snapshot: 'juniper:snapshot',
  send: 'juniper:send',
  cancel: 'juniper:cancel',
  newChat: 'juniper:new',
  sessions: 'juniper:sessions',
  resume: 'juniper:resume',
  login: 'juniper:login',
  logout: 'juniper:logout',
  pauseFiles: 'juniper:pause-files',
  event: 'juniper:event',
  voiceStart: 'juniper:voice-start',
  voiceEnd: 'juniper:voice-end',
  voiceInterrupt: 'juniper:voice-interrupt',
  audioEvent: 'juniper:audio-event',
  updateApply: 'juniper:update-apply',
  updateCheck: 'juniper:update-check',
  audioCommand: 'juniper:audio-command',
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
  /** Asks the brain for the list of conversations (it arrives in the next snapshot). */
  sessions(): Promise<void>;
  resume(sessionId: string): Promise<void>;
  login(input: LoginInput): Promise<{ ok: boolean; error?: string }>;
  logout(): Promise<void>;
  pauseFiles(paused: boolean): Promise<void>;
  onEvent(callback: (snapshot: UiSnapshot) => void): () => void;
  updateApply(): Promise<void>;
  updateCheck(): Promise<void>;
  voiceStart(): Promise<boolean>;
  voiceEnd(): Promise<void>;
  voiceInterrupt(): Promise<void>;
  /** What the audio engine in the window reports (chunks, acknowledgements, headphones). */
  audioEvent(event: AudioEvent): Promise<void>;
  onAudioCommand(callback: (command: AudioCommand) => void): () => void;
}
