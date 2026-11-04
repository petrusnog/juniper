/**
 * Electron main process: window, connection, executor and the secure store (ADR-033).
 * It composes the pure core with the real system; the renderer sees none of it.
 */
import { app, BrowserWindow, ipcMain, safeStorage, session, shell } from 'electron';
import type { IpcMainInvokeEvent } from 'electron';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import WebSocket from 'ws';
import { ChatController } from '../core/chat/ChatController';
import { BrainConnection } from '../core/connection/BrainConnection';
import type { SocketLike } from '../core/connection/BrainConnection';
import { checkEndpoint } from '../core/connection/endpoint';
import { bearerOf, mask } from '../core/connection/credential';
import { Executor } from '../core/executor/Executor';
import type { ActivityEntry } from '../core/executor/Executor';
import { openUrlTool } from '../core/executor/openUrl';
import { initialVoice, VoiceController } from '../core/voice/VoiceController';
import { RemoteAudio } from '../core/voice/RemoteAudio';
import { noStt, noTts } from '../core/voice/devices';
import { parseAudioEvent } from '../shared/audioProtocol';
import type { AudioEvent } from '../shared/audioProtocol';
import { CHANNELS } from '../shared/ipc';
import type { LoginInput, UiSnapshot } from '../shared/ipc';
import { CredentialStore } from './credentialStore';
import { createFileTools } from './executor/fs/tools';
import { UpdateController } from '../core/update/UpdateController';
import { PUBLISHER } from './buildInfo';
import { currentPlatform, deviceName } from './platform';
import { createUpdatePorts } from './update/ports';

const VERSION = app.getVersion();
const FILE_TOOLS_PREFIX = 'fs_';

let window: BrowserWindow | null = null;
let connection: BrainConnection | null = null;
let chat: ChatController | null = null;
let executor: Executor | null = null;
let voice: VoiceController | null = null;
let audio: RemoteAudio | null = null;
let updater: UpdateController | null = null;
let updateTimer: ReturnType<typeof setInterval> | null = null;
const UPDATE_CHECK_MS = 6 * 60 * 60 * 1000;
const audioListeners = new Set<(event: AudioEvent) => void>();
let filesPaused = false;
let store: CredentialStore;
let logFile: string;

function log(entry: ActivityEntry): void {
  // Operation, tool and outcome only: never a path's content, never a secret (ADR-033).
  fs.appendFile(logFile, `${mask(JSON.stringify(entry))}\n`, () => undefined);
}

function snapshot(): UiSnapshot {
  return {
    connection: connection?.state ?? 'idle',
    chat: chat?.getState() ?? {
      sessionId: null,
      messages: [],
      activeTurn: null,
      pendingSend: null,
      sessions: null,
    },
    activity: executor?.activity.slice(-30) ?? [],
    filesPaused,
    hasCredential: connection !== null,
    secureStorage: store.secure,
    voice: voice?.getState() ?? initialVoice,
    update: updater?.state ?? { phase: 'none' },
    version: VERSION,
  };
}

function push(): void {
  window?.webContents.send(CHANNELS.event, snapshot());
}

function wsFactory(url: string, headers: Record<string, string>): SocketLike {
  // Certificate validation stays on (the default); `maxPayload` bounds a frame.
  const socket = new WebSocket(url, { headers, maxPayload: 4 * 1024 * 1024, handshakeTimeout: 10_000 });
  return socket as unknown as SocketLike; // `ws` event handlers are a superset of ours
}

function teardown(): void {
  if (updateTimer !== null) clearInterval(updateTimer);
  updateTimer = null;
  updater = null;
  void voice?.end();
  audio?.dispose();
  voice = null;
  audio = null;
  connection?.close();
  executor?.stop();
  chat?.stop();
}

async function connect(login: { url: string; clientId: string; clientSecret: string; deviceId: string }) {
  teardown();
  const endpoint = checkEndpoint(login.url);
  if (!endpoint.ok || endpoint.url === undefined) return endpoint.reason ?? 'endereço recusado';
  const platform = currentPlatform(app.getPath('userData'));
  const conn = new BrainConnection({
    url: endpoint.url,
    bearer: bearerOf(login),
    deviceId: login.deviceId,
    deviceName: deviceName(),
    appVersion: VERSION,
    capabilities: { fs: true },
    resume: () => chat?.resumeTarget(),
    createSocket: wsFactory,
  });
  connection = conn;
  chat = new ChatController(conn);
  executor = new Executor(
    conn,
    [
      openUrlTool({ open: url => shell.openExternal(url) }),
      ...createFileTools({ rules: platform, trash: { trash: p => shell.trashItem(p) } }),
    ],
    Date.now,
    log,
  );
  executor.setPaused(FILE_TOOLS_PREFIX, filesPaused);
  conn.onState(push);
  chat.subscribe(push);
  audio = new RemoteAudio({
    send: command => window?.webContents.send(CHANNELS.audioCommand, command),
    onEvent: listener => {
      audioListeners.add(listener);
      return () => audioListeners.delete(listener);
    },
  });
  voice = new VoiceController(conn, audio, noTts, noStt);
  voice.subscribe(push);
  updater = new UpdateController(
    createUpdatePorts({
      wsUrl: endpoint.url,
      bearer: bearerOf(login),
      workDir: path.join(app.getPath('userData'), 'updates'),
      quit: () => app.quit(),
    }),
    VERSION,
    PUBLISHER,
  );
  updater.subscribe(push);
  conn.onState(state => {
    if (state === 'connected') void updater?.check();
  });
  updateTimer = setInterval(() => void updater?.check(), UPDATE_CHECK_MS);
  chat.start();
  executor.start();
  conn.connect();
  return null;
}

const LOGIN_FAILURE: Record<string, string> = {
  unauthorized: 'O cérebro recusou essa credencial.',
  incompatible: 'O cérebro e o app falam versões diferentes do protocolo.',
  timeout: 'O cérebro não respondeu (confira o endereço e o Tailscale).',
};

/** Waits until the connection is up or definitely refused (or `ms` pass). */
function settled(conn: BrainConnection | null, ms: number): Promise<string> {
  return new Promise(resolve => {
    if (conn === null) return resolve('timeout');
    const done = (state: string) => {
      clearTimeout(timer);
      off();
      resolve(state);
    };
    const timer = setTimeout(() => done('timeout'), ms);
    const off = conn.onState(state => {
      if (['connected', 'unauthorized', 'incompatible', 'replaced'].includes(state)) done(state);
    });
    if (conn.state === 'connected') done('connected');
  });
}

function trustedSender(event: IpcMainInvokeEvent): boolean {
  const frame = event.senderFrame;
  return (
    window !== null &&
    frame !== null &&
    frame === window.webContents.mainFrame &&
    frame.url.startsWith('file://')
  );
}

function handle<T>(channel: string, fn: (event: IpcMainInvokeEvent, ...args: never[]) => T): void {
  ipcMain.handle(channel, (event, ...args) => {
    if (!trustedSender(event)) throw new Error('IPC from an untrusted frame');
    return (fn as (e: IpcMainInvokeEvent, ...a: unknown[]) => T)(event, ...args);
  });
}

function registerIpc(): void {
  handle(CHANNELS.snapshot, () => snapshot());
  handle(CHANNELS.send, (_e, text: string) => chat?.send(text) ?? false);
  handle(CHANNELS.cancel, () => chat?.cancel());
  handle(CHANNELS.newChat, () => chat?.newConversation());
  handle(CHANNELS.resume, (_e, id: string) => chat?.resume(id));
  handle(CHANNELS.pauseFiles, (_e, paused: boolean) => {
    filesPaused = paused === true;
    executor?.setPaused(FILE_TOOLS_PREFIX, filesPaused);
    push();
  });
  handle(CHANNELS.updateApply, () => updater?.apply());
  handle(CHANNELS.updateCheck, () => updater?.check());
  handle(CHANNELS.voiceStart, () => voice?.start() ?? false);
  handle(CHANNELS.voiceEnd, () => voice?.end());
  handle(CHANNELS.voiceInterrupt, () => voice?.interrupt());
  handle(CHANNELS.audioEvent, (_e, event: unknown) => {
    const valid = parseAudioEvent(event);
    if (valid !== null) audioListeners.forEach(l => l(valid));
  });
  handle(CHANNELS.logout, async () => {
    teardown();
    connection = null;
    chat = null;
    executor = null;
    await store.clear();
    push();
  });
  handle(CHANNELS.login, async (_e, input: LoginInput) => {
    if (!store.secure) {
      return { ok: false, error: 'Este Windows não oferece armazenamento seguro; não vou guardar a credencial.' };
    }
    const login = {
      url: input.url,
      clientId: input.clientId.trim(),
      clientSecret: input.clientSecret.trim(),
      deviceId: (await store.load())?.deviceId ?? crypto.randomUUID(),
    };
    const error = await connect(login);
    if (error !== null) return { ok: false, error };
    // Saved only once the brain accepted it: a wrong credential is never kept.
    const outcome = await settled(connection, 10_000);
    if (outcome !== 'connected') {
      connection?.close();
      return { ok: false, error: LOGIN_FAILURE[outcome] ?? 'Não consegui conectar.' };
    }
    await store.save(login);
    return { ok: true };
  });
}

function createWindow(): void {
  window = new BrowserWindow({
    width: 900,
    height: 700,
    title: 'Juniper',
    backgroundColor: '#0b0f14',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload', 'index.js'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      webviewTag: false,
      allowRunningInsecureContent: false,
      devTools: !app.isPackaged,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', e => e.preventDefault());
  window.webContents.on('will-redirect', e => e.preventDefault());
  void window.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => window?.focus()); // arguments are ignored on purpose
  void app.whenReady().then(async () => {
    // Only the microphone, only for our own window (the voice chat); everything else is denied.
    const page = pathToFileURL(path.join(__dirname, '..', 'renderer', 'index.html')).toString();
    const ours = (url: string | undefined): boolean => (url ?? '').startsWith('file://') && (url ?? '') === page;
    session.defaultSession.setPermissionRequestHandler((_wc, permission, cb, details) => {
      const types = (details as { mediaTypes?: string[] }).mediaTypes ?? [];
      const wantsAudioOnly = permission === 'media' && types.length > 0 && types.every(t => t === 'audio');
      cb(wantsAudioOnly && ours(details.requestingUrl));
    });
    session.defaultSession.setPermissionCheckHandler(
      (_wc, permission, origin) => permission === 'media' && ours(origin),
    );
    session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
      cb({
        responseHeaders: {
          ...details.responseHeaders,
          'Content-Security-Policy': [
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'; img-src 'self'; base-uri 'none'; form-action 'none'",
          ],
        },
      });
    });
    const userData = app.getPath('userData');
    fs.mkdirSync(userData, { recursive: true });
    logFile = path.join(userData, 'activity.log');
    store = new CredentialStore(path.join(userData, 'credential.bin'), {
      isAvailable: () => safeStorage.isEncryptionAvailable(),
      encrypt: t => safeStorage.encryptString(t),
      decrypt: b => safeStorage.decryptString(b),
    });
    registerIpc();
    createWindow();
    const saved = await store.load();
    if (saved !== null) await connect(saved);
    push();
  });
  app.on('window-all-closed', () => app.quit());
}
