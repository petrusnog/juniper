import { WakeWordDetector } from './WakeWordDetector';

/** The keyword file the person trained for "Juniper": android/app/src/main/assets/juniper.ppn. */
export const KEYWORD_ASSET = 'juniper.ppn';

type PorcupineManagerLike = {
  start(): Promise<void>;
  stop(): Promise<void>;
  delete(): void;
};

/**
 * Picovoice Porcupine: a small offline engine that only recognises the trained word. It needs a
 * free AccessKey (typed once in the app's settings, kept in the Keystore) and a "Juniper" keyword
 * file made in the Picovoice console (docs/pages/mobile.md). Loaded lazily, so the rest of the
 * app (and the tests) never need the native module.
 */
export class PorcupineDetector implements WakeWordDetector {
  private manager: PorcupineManagerLike | null = null;

  constructor(private readonly accessKey: string | null) {}

  get available(): boolean {
    return !!this.accessKey && this.accessKey.trim().length > 0;
  }

  async start(
    onWake: () => void,
    onError: (message: string) => void,
  ): Promise<void> {
    if (!this.available) {
      throw new Error('missing_access_key');
    }
    await this.stop();
    const { PorcupineManager } = require('@picovoice/porcupine-react-native');
    this.manager = (await PorcupineManager.fromKeywordPaths(
      this.accessKey!.trim(),
      [KEYWORD_ASSET],
      () => onWake(),
      (error: { message?: string }) =>
        onError(error?.message ?? 'porcupine_error'),
    )) as PorcupineManagerLike;
    await this.manager.start();
  }

  async stop(): Promise<void> {
    const manager = this.manager;
    this.manager = null;
    if (manager) {
      try {
        await manager.stop();
      } finally {
        manager.delete();
      }
    }
  }
}
