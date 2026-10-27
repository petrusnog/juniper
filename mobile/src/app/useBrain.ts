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

export const APP_VERSION = '0.1.0';

/** Owns the connection and the chat for the lifetime of the credentials. */
export function useBrain(credentials: Credentials | null) {
  const [state, setState] = useState<ConnectionState>('idle');
  const [live, setLive] = useState<{
    connection: BrainConnection;
    chat: ChatController;
    voice: VoiceController;
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
    deviceTts.probe().catch(() => undefined);
    ref.current = connection;
    setLive({ connection, chat, voice });
    const off = connection.onState(setState);
    connection.connect();
    return () => {
      off();
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
    state,
    reconnect: () => ref.current?.connect(),
  };
}
