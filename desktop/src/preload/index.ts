/** The minimal bridge (contextBridge): fixed channels, no generic IPC, no path ever. */
import { contextBridge, ipcRenderer } from 'electron';
import { CHANNELS } from '../shared/ipc';
import type { AudioCommand } from '../shared/audioProtocol';
import type { JuniperBridge, UiSnapshot } from '../shared/ipc';

const bridge: JuniperBridge = {
  snapshot: () => ipcRenderer.invoke(CHANNELS.snapshot),
  send: text => ipcRenderer.invoke(CHANNELS.send, String(text)),
  cancel: () => ipcRenderer.invoke(CHANNELS.cancel),
  newChat: () => ipcRenderer.invoke(CHANNELS.newChat),
  resume: id => ipcRenderer.invoke(CHANNELS.resume, String(id)),
  login: input =>
    ipcRenderer.invoke(CHANNELS.login, {
      url: String(input.url),
      clientId: String(input.clientId),
      clientSecret: String(input.clientSecret),
    }),
  logout: () => ipcRenderer.invoke(CHANNELS.logout),
  pauseFiles: paused => ipcRenderer.invoke(CHANNELS.pauseFiles, paused === true),
  onEvent: callback => {
    const listener = (_e: unknown, snapshot: UiSnapshot): void => callback(snapshot);
    ipcRenderer.on(CHANNELS.event, listener);
    return () => ipcRenderer.removeListener(CHANNELS.event, listener);
  },
  voiceStart: () => ipcRenderer.invoke(CHANNELS.voiceStart),
  voiceEnd: () => ipcRenderer.invoke(CHANNELS.voiceEnd),
  voiceInterrupt: () => ipcRenderer.invoke(CHANNELS.voiceInterrupt),
  audioEvent: event => ipcRenderer.invoke(CHANNELS.audioEvent, event),
  onAudioCommand: callback => {
    const listener = (_e: unknown, command: AudioCommand): void => callback(command);
    ipcRenderer.on(CHANNELS.audioCommand, listener);
    return () => ipcRenderer.removeListener(CHANNELS.audioCommand, listener);
  },
};

contextBridge.exposeInMainWorld('juniper', bridge);
