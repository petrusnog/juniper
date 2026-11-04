/**
 * Electron main process: window, connection, executor and the secure store (ADR-033).
 * It composes the pure core with the real system; the renderer sees none of it.
 */
import { app, BrowserWindow, ipcMain, safeStorage, session, shell } from 'electron';
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
import { Executor } from '../core/executor/Executor';
import type { ActivityEntry } from '../core/executor/Executor';
import { openUrlTool } from '../core/executor/openUrl';
import { CHANNELS } from '../shared/ipc';
import type { LoginInput, UiSnapshot } from '../shared/ipc';
import { CredentialStore } from './credentialStore';
import { createFileTools } from './executor/fs/tools';
import { currentPlatform, deviceName } from './platform';

const VERSION = app.getVersion();
const FILE_TOOLS_PREFIX = 'fs_';

let window: BrowserWindow | null = null;
let connection: BrainConnection | null = null;
let chat: ChatController | null = null;
let executor: Executor | null = null;
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

async function connect(login: { url: string; clientId: string; clientSecret: string; deviceId: string }) {
  connection?.close();
  executor?.stop();
  chat?.stop();
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
  chat.start();
  executor.start();
  conn.connect();
  return null;
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
  handle(CHANNELS.logout, async () => {
    connection?.close();
    executor?.stop();
    chat?.stop();
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
    session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
    session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
      cb({
        responseHeaders: {
          ...details.responseHeaders,
          'Content-Security-Policy': [
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'; img-src 'self'",
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
