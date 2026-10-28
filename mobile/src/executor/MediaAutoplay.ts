import { NativeModules } from 'react-native';

export interface AutoplayOptions {
  /** How long to wait for the music app to open before the first play key. */
  waitMs: number;
  attempts: number;
  retryMs: number;
}

export const DEFAULT_AUTOPLAY: AutoplayOptions = {
  waitMs: 3000,
  attempts: 3,
  retryMs: 1500,
};

export interface OpenResult {
  opened: boolean;
  /** true: music is playing; false: it opened but did not start; null: it did not open. */
  autoplay: boolean | null;
  detail?: string;
}

/** The only thing the app knows about the music app: open this link and try to play it. */
export interface MediaAutoplay {
  openAndPlay(
    url: string,
    options?: Partial<AutoplayOptions>,
  ): Promise<OpenResult>;
}

/** The Kotlin module `JuniperMedia` (android/.../media/MediaAutoplayModule.kt). */
export const nativeMediaAutoplay: MediaAutoplay = {
  async openAndPlay(url, options = {}) {
    const native = NativeModules.JuniperMedia as
      | { openAndPlay(u: string, o: AutoplayOptions): Promise<OpenResult> }
      | undefined;
    if (!native) {
      return { opened: false, autoplay: null, detail: 'native_module_missing' };
    }
    try {
      return await native.openAndPlay(url, { ...DEFAULT_AUTOPLAY, ...options });
    } catch {
      return { opened: false, autoplay: null, detail: 'native_error' };
    }
  },
};
