/**
 * Electron main process: window, connection, executor and the secure store (ADR-033).
 * It composes the pure core with the real system; the renderer sees none of it.
 */
import { app, BrowserWindow, ipcMain, Menu, nativeImage, protocol, safeStorage, session, shell } from 'electron';
import type { IpcMainInvokeEvent } from 'electron';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import WebSocket from 'ws';
import { ChatController } from '../core/chat/ChatController';
import { BrainConnection } from '../core/connection/BrainConnection';
import type { SocketLike } from '../core/connection/BrainConnection';
import { checkEndpoint } from '../core/connection/endpoint';
import { bearerOf, mask } from '../core/connection/credential';
import { hintFor } from '../core/connection/diagnose';
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
import { canAutoApply, UpdateController } from '../core/update/UpdateController';
import { isBusy } from '../core/chat/state';
import { PUBLISHER } from './buildInfo';
import { currentPlatform, deviceName } from './platform';
import { createUpdatePorts } from './update/ports';

const VERSION = app.getVersion();

// The window is served by our own scheme, not file://: with the `GrantFileProtocolExtraPrivileges`
// fuse off (ADR-033) a file:// page inside the asar does not load at all. `app://juniper/` is a
// standard, secure origin, so CSP `'self'`, the microphone and the AudioWorklet work, and only the
// renderer folder can be read through it.
const APP_ORIGIN = 'app://juniper';
const APP_PAGE = `${APP_ORIGIN}/index.html`;
protocol.registerSchemesAsPrivileged([
  { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);
const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.png': 'image/png',
};

function serveRenderer(): void {
  const root = path.join(__dirname, '..', 'renderer');
  protocol.handle('app', async request => {
    const url = new URL(request.url);
    const requested = decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname);
    const file = path.normalize(path.join(root, requested));
    const type = MIME[path.extname(file).toLowerCase()];
    // Nothing outside the renderer folder, and only the three kinds of file the window uses.
    if (url.host !== 'juniper' || !file.startsWith(root + path.sep) || type === undefined) {
      return new Response('Not found', { status: 404 });
    }
    try {
      return new Response(await fs.promises.readFile(file), { headers: { 'Content-Type': type } });
    } catch {
      return new Response('Not found', { status: 404 });
    }
  });
}
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
const AUTO_UPDATE_GRACE_MS = 8000; // the person sees "há uma versão nova" before it goes ahead
let autoUpdateTimer: ReturnType<typeof setTimeout> | null = null;
const audioListeners = new Set<(event: AudioEvent) => void>();
let filesPaused = false;
let store: CredentialStore;
let logFile: string;
let connectionLog: string;

function log(entry: ActivityEntry): void {
  // Operation, tool and outcome only: never a path's content, never a secret (ADR-033).
  fs.appendFile(logFile, `${mask(JSON.stringify(entry))}\n`, () => undefined);
}

function snapshot(): UiSnapshot {
  const detail = connection?.state === 'connected' ? null : (connection?.detail ?? null);
  return {
    connection: connection?.state ?? 'idle',
    connectionDetail: detail,
    connectionHint: hintFor(detail),
    chat: chat?.getState() ?? {
      sessionId: null,
      title: '',
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

/**
 * Applies a waiting update by itself, but only while nothing is going on (no voice call, no answer
 * being written). Called after every change of the update, the chat or the voice, so a busy moment
 * only postpones it.
 */
function scheduleAutoUpdate(): void {
  if (autoUpdateTimer !== null) clearTimeout(autoUpdateTimer);
  autoUpdateTimer = setTimeout(() => {
    autoUpdateTimer = null;
    if (updater === null) return;
    const ready = canAutoApply({
      update: updater.state,
      voicePhase: voice?.getState().phase ?? 'off',
      chatBusy: chat !== null && isBusy(chat.getState()),
    });
    if (ready) void updater.apply();
  }, AUTO_UPDATE_GRACE_MS);
}

function teardown(): void {
  if (autoUpdateTimer !== null) clearTimeout(autoUpdateTimer);
  autoUpdateTimer = null;
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
  conn.onState(state => {
    // Every transition, with the reason, never the credential: this is the file to look at when
    // the app cannot connect (userData/connection.log).
    fs.appendFile(
      connectionLog,
      `${mask(JSON.stringify({ at: new Date().toISOString(), state, url: endpoint.url, detail: conn.detail }))}\n`,
      () => undefined,
    );
    push();
  });
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
  updater.subscribe(() => {
    push();
    scheduleAutoUpdate();
  });
  chat.subscribe(scheduleAutoUpdate);
  voice.subscribe(scheduleAutoUpdate);
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
    frame.url.startsWith(`${APP_ORIGIN}/`)
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
  handle(CHANNELS.sessions, () => chat?.refreshSessions());
  handle(CHANNELS.resume, (_e, id: string) => chat?.resume(id));
  handle(CHANNELS.renameSession, (_e, id: string, title: string) => chat?.rename(title, id) ?? false);
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
      const why = connection?.detail ?? null;
      const base = LOGIN_FAILURE[outcome] ?? 'Não consegui conectar.';
      const hint = hintFor(why);
      return { ok: false, error: [base, hint ?? (why ? `Detalhe: ${why}` : null)].filter(Boolean).join(' ') };
    }
    await store.save(login);
    return { ok: true };
  });
}

function createWindow(): void {
  if (app.isPackaged) Menu.setApplicationMenu(null); // no File/Edit/View bar in the installed app
  window = new BrowserWindow({
    width: 900,
    height: 700,
    title: 'Juniper',
    backgroundColor: '#000000', // the terminal black (tokens.ts), also while the page loads
    icon: nativeImage.createFromPath(path.join(__dirname, '..', 'renderer', 'logo.png')),
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
  void window.loadURL(APP_PAGE);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => window?.focus()); // arguments are ignored on purpose
  void app.whenReady().then(async () => {
    // Only the microphone, only for our own window (the voice chat); everything else is denied.
    const ours = (url: string | undefined): boolean => url === APP_PAGE;
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
    connectionLog = path.join(userData, 'connection.log');
    store = new CredentialStore(path.join(userData, 'credential.bin'), {
      isAvailable: () => safeStorage.isEncryptionAvailable(),
      encrypt: t => safeStorage.encryptString(t),
      decrypt: b => safeStorage.decryptString(b),
    });
    serveRenderer();
    registerIpc();
    createWindow();
    const saved = await store.load();
    if (saved !== null) await connect(saved);
    push();
  });
  app.on('window-all-closed', () => app.quit());
}
