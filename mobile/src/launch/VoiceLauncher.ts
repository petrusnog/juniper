/**
 * Opens the hands-free voice flow from outside the voice screen: the link `juniper://voz`
 * (home-screen shortcut, the notification button, "Ok Google, abre a Juniper"). The brain
 * greets, the person asks, the music plays and the session closes (`wake: true`, ADR-023).
 */
import type {
  BrainConnection,
  ConnectionState,
} from '../connection/BrainConnection';
import type { VoiceController } from '../voice/VoiceController';

export const LAUNCH_URL = 'juniper://voz';

/** How long a cold start waits for the connection before giving up. */
export const CONNECT_WAIT_MS = 8000;

export function isLaunchUrl(url: string | null | undefined): boolean {
  return typeof url === 'string' && url.trim().toLowerCase() === LAUNCH_URL;
}

export class VoiceLauncher {
  constructor(
    private readonly connection: Pick<BrainConnection, 'state' | 'onState'>,
    private readonly voice: Pick<VoiceController, 'state' | 'start'>,
    private readonly waitMs: number = CONNECT_WAIT_MS,
  ) {}

  /** Starts the voice session once connected. `false` if it was already on or did not start. */
  async trigger(): Promise<boolean> {
    if (this.voice.state.phase !== 'off') {
      return false; // already talking: a second tap changes nothing
    }
    await this.connected(); // offline after the wait: `start` explains it to the person
    return this.voice.start({ wake: true });
  }

  private connected(): Promise<boolean> {
    if (this.connection.state === 'connected') {
      return Promise.resolve(true);
    }
    return new Promise(resolve => {
      let off: () => void = () => undefined;
      const timer = setTimeout(() => {
        off();
        resolve(false);
      }, this.waitMs);
      off = this.connection.onState((state: ConnectionState) => {
        if (state === 'connected') {
          clearTimeout(timer);
          off();
          resolve(true);
        }
      });
    });
  }
}
