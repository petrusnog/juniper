import { useEffect, useRef, useState } from 'react';
import { ChatController } from '../chat/ChatController';
import {
  BrainConnection,
  ConnectionState,
} from '../connection/BrainConnection';
import { nativeMediaAutoplay } from '../executor/MediaAutoplay';
import { OpenUrlExecutor } from '../executor/OpenUrlExecutor';
import { Credentials, serverUrl } from '../storage/credentials';
import { nativeAudio } from '../voice/nativeAudio';
import { deviceStt } from '../voice/nativeStt';
import { deviceTts } from '../voice/nativeTts';
import { VoiceController } from '../voice/VoiceController';
import { PorcupineDetector } from '../wake/porcupine';
import { WakeController } from '../wake/WakeController';

export const APP_VERSION = '0.1.0';

/** Owns the connection and the chat for the lifetime of the credentials. */
export function useBrain(credentials: Credentials | null) {
  const [state, setState] = useState<ConnectionState>('idle');
  const [live, setLive] = useState<{
    connection: BrainConnection;
    chat: ChatController;
    voice: VoiceController;
    wake: WakeController;
  } | null>(null);
  const ref = useRef<BrainConnection | null>(null);

  useEffect(() => {
    if (credentials === null) {
      return undefined;
    }
    const holder: { chat: ChatController | null } = { chat: null };
    const connection = new BrainConnection({
      url: serverUrl(credentials),
      token: credentials.token,
      deviceId: credentials.deviceId,
      deviceName: credentials.deviceName,
      appVersion: APP_VERSION,
      resume: () => holder.chat?.resumeTarget(),
    });
    const chat = new ChatController(connection);
    holder.chat = chat;
    chat.start();
    const executor = new OpenUrlExecutor(connection, nativeMediaAutoplay);
    executor.start();
    const voice = new VoiceController(
      connection,
      nativeAudio,
      deviceTts,
      deviceStt,
    );
    const wake = new WakeController(
      connection,
      voice,
      nativeAudio,
      new PorcupineDetector(credentials.picovoiceKey ?? null),
    );
    deviceTts.probe().catch(() => undefined);
    ref.current = connection;
    setLive({ connection, chat, voice, wake });
    if (credentials.wakeEnabled) {
      wake.enable().catch(() => undefined); // hands-free mode was left on: wait for the word
    }
    const off = connection.onState(setState);
    connection.connect();
    return () => {
      off();
      wake.disable().catch(() => undefined);
      voice.end().catch(() => undefined);
      chat.stop();
      executor.stop();
      connection.close();
      ref.current = null;
      setLive(null);
      setState('idle');
    };
  }, [credentials]);

  return {
    connection: live?.connection ?? null,
    chat: live?.chat ?? null,
    voice: live?.voice ?? null,
    wake: live?.wake ?? null,
    state,
    reconnect: () => ref.current?.connect(),
  };
}
