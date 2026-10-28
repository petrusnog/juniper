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
  /** The app's screen was in front when the link was opened. */
  foreground?: boolean;
  /** "Display over other apps" is granted (what lets us open it from the background). */
  overlay?: boolean;
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

/** The "display over other apps" permission, which lets the music open from the background. */
export const backgroundPermission = {
  async granted(): Promise<boolean> {
    const native = NativeModules.JuniperMedia as
      | { canOpenFromBackground(): Promise<boolean> }
      | undefined;
    try {
      return (await native?.canOpenFromBackground()) === true;
    } catch {
      return false;
    }
  },
  /** Opens the Android screen where the person grants it. */
  openSettings(): void {
    (
      NativeModules.JuniperMedia as { openOverlaySettings(): void } | undefined
    )?.openOverlaySettings();
  },
};
