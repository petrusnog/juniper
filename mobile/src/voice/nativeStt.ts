import { NativeEventEmitter, NativeModules } from 'react-native';
import { DeviceStt } from './devices';

interface NativeStt {
  isAvailable(): Promise<boolean>;
  start(locale: string): void;
  stop(): void;
}

const LOCALE = 'pt-BR';
const RESTART_MS = 300;
const RESTART_AFTER_ERROR_MS = 1000;
/** SpeechRecognizer error codes after which listening again is pointless (permissions, no engine). */
const FATAL = new Set([
  -1, 9 /* INSUFFICIENT_PERMISSIONS */, 12, 13 /* language unavailable */,
]);

/**
 * The Android speech recognizer (pt-BR) through our own Kotlin module `JuniperStt`, for when the
 * cloud STT is out (ADR-023, section 5). The module recognises one utterance per `start`; this
 * restarts it after each result or silence, so it listens continuously until `stop`.
 */
class DeviceSttImpl implements DeviceStt {
  private wanted = false;
  private subscriptions: { remove(): void }[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;

  private get native(): NativeStt | undefined {
    return NativeModules.JuniperStt as NativeStt | undefined;
  }

  get available(): boolean {
    return this.native !== undefined;
  }

  async start(
    onText: (text: string) => void,
    onError: (message: string) => void,
  ) {
    const native = this.native;
    if (!native) {
      throw new Error('native_module_missing');
    }
    await this.stop();
    this.wanted = true;
    const emitter = new NativeEventEmitter(NativeModules.JuniperStt);
    const again = (ms: number) => {
      if (this.timer !== null) {
        clearTimeout(this.timer);
      }
      this.timer = setTimeout(() => {
        this.timer = null;
        if (this.wanted) {
          native.start(LOCALE);
        }
      }, ms);
    };
    this.subscriptions = [
      emitter.addListener('juniperSttResult', (text: unknown) => {
        onText(String(text));
        again(RESTART_MS);
      }),
      emitter.addListener('juniperSttError', (code: unknown) => {
        const n = Number(code);
        if (FATAL.has(n)) {
          this.wanted = false;
          onError(`speech_error_${n}`);
          return;
        }
        again(
          n === 7 /* NO_MATCH */ || n === 6 /* SPEECH_TIMEOUT */
            ? RESTART_MS
            : RESTART_AFTER_ERROR_MS,
        );
      }),
    ];
    native.start(LOCALE);
  }

  async stop() {
    this.wanted = false;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.subscriptions.forEach(s => s.remove());
    this.subscriptions = [];
    this.native?.stop();
  }
}

export const deviceStt = new DeviceSttImpl();
