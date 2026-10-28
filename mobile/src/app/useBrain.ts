import { useEffect, useRef, useState } from 'react';
import {
  BrainConnection,
  ConnectionState,
} from '../connection/BrainConnection';
import { Credentials, serverUrl } from '../storage/credentials';

export const APP_VERSION = '0.1.0';

/** Owns the connection for the lifetime of the credentials; closes it when they change. */
export function useBrain(credentials: Credentials | null) {
  const [state, setState] = useState<ConnectionState>('idle');
  const [connection, setConnection] = useState<BrainConnection | null>(null);
  const ref = useRef<BrainConnection | null>(null);

  useEffect(() => {
    if (credentials === null) {
      return undefined;
    }
    const next = new BrainConnection({
      url: serverUrl(credentials),
      token: credentials.token,
      deviceId: credentials.deviceId,
      deviceName: credentials.deviceName,
      appVersion: APP_VERSION,
    });
    ref.current = next;
    setConnection(next);
    const off = next.onState(setState);
    next.connect();
    return () => {
      off();
      next.close();
      ref.current = null;
      setConnection(null);
      setState('idle');
    };
  }, [credentials]);

  return { connection, state, reconnect: () => ref.current?.connect() };
}
