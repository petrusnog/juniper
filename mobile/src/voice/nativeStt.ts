import { DeviceStt } from './devices';

type VoiceLib = typeof import('@react-native-voice/voice').default;

const LOCALE = 'pt-BR';
const RESTART_MS = 300;

/**
 * The Android speech recognizer (pt-BR) through `@react-native-voice/voice`, for when the cloud
 * STT is out (ADR-023, section 5). It listens continuously: each final result goes to `onText`
 * and recognition restarts. Loaded lazily, so tests and screens that never use it do not need the
 * native module.
 */
class DeviceSttImpl implements DeviceStt {
  available = true;
  private wanted = false;
  private lib: VoiceLib | null = null;

  private voice(): VoiceLib {
    if (this.lib === null) {
      this.lib = require('@react-native-voice/voice').default as VoiceLib;
    }
    return this.lib;
  }

  async start(
    onText: (text: string) => void,
    onError: (message: string) => void,
  ) {
    const voice = this.voice();
    this.wanted = true;
    const listen = () => {
      if (this.wanted) {
        voice.start(LOCALE).catch(e => onError(String(e)));
      }
    };
    voice.onSpeechResults = e => {
      const text = e.value?.[0];
      if (text) {
        onText(text);
      }
    };
    voice.onSpeechEnd = () => setTimeout(listen, RESTART_MS);
    voice.onSpeechError = e => {
      onError(e.error?.message ?? 'speech_error');
      setTimeout(listen, RESTART_MS * 3);
    };
    listen();
  }

  async stop() {
    this.wanted = false;
    if (this.lib !== null) {
      await this.lib.stop().catch(() => undefined);
      this.lib.removeAllListeners();
    }
  }
}

export const deviceStt = new DeviceSttImpl();
