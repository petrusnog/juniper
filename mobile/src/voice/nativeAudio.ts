import {
  NativeEventEmitter,
  NativeModules,
  PermissionsAndroid,
  Platform,
} from 'react-native';
import { AudioDevice } from './devices';

interface NativeAudio {
  start(sampleRate: number): Promise<void>;
  stop(): Promise<void>;
  pauseCapture(): Promise<void>;
  resumeCapture(): Promise<void>;
  hasHeadset(): Promise<boolean>;
  applyRoute(speaker: boolean): void;
  openPlayer(sampleRate: number): Promise<void>;
  play(base64: string): void;
  flush(): void;
  closePlayer(): Promise<void>;
}

async function ensurePermissions(): Promise<void> {
  const mic = await PermissionsAndroid.request(
    PermissionsAndroid.PERMISSIONS.RECORD_AUDIO,
  );
  if (mic !== PermissionsAndroid.RESULTS.GRANTED) {
    throw new Error('microphone_permission_denied');
  }
  if (Platform.OS === 'android' && Platform.Version >= 33) {
    // Without it the "Juniper está ouvindo" notification is hidden; the session still works.
    await PermissionsAndroid.request(
      PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS,
    );
  }
}

/** The Kotlin module `JuniperAudio` (android/.../voice/JuniperAudioModule.kt). */
export const nativeAudio: AudioDevice = {
  async start(sampleRate) {
    const native = NativeModules.JuniperAudio as NativeAudio | undefined;
    if (!native) {
      throw new Error('native_module_missing');
    }
    await ensurePermissions();
    await native.start(sampleRate);
  },
  async stop() {
    await (NativeModules.JuniperAudio as NativeAudio | undefined)?.stop();
  },
  onChunk(listener) {
    const emitter = new NativeEventEmitter(NativeModules.JuniperAudio);
    const subscription = emitter.addListener(
      'juniperAudioChunk',
      (data: unknown) => listener(String(data)),
    );
    return () => subscription.remove();
  },
  onStopRequested(listener) {
    const emitter = new NativeEventEmitter(NativeModules.JuniperAudio);
    const subscription = emitter.addListener('juniperStopRequested', listener);
    return () => subscription.remove();
  },
  async pauseCapture() {
    await (
      NativeModules.JuniperAudio as NativeAudio | undefined
    )?.pauseCapture();
  },
  async resumeCapture() {
    await (
      NativeModules.JuniperAudio as NativeAudio | undefined
    )?.resumeCapture();
  },
  async hasHeadset() {
    return (
      (await (
        NativeModules.JuniperAudio as NativeAudio | undefined
      )?.hasHeadset()) === true
    );
  },
  async applyRoute(speaker) {
    (NativeModules.JuniperAudio as NativeAudio | undefined)?.applyRoute(
      speaker,
    );
  },
  onRouteChange(listener) {
    const emitter = new NativeEventEmitter(NativeModules.JuniperAudio);
    const subscription = emitter.addListener(
      'juniperAudioRoute',
      (headset: unknown) => listener(headset === true),
    );
    return () => subscription.remove();
  },
  async openPlayer(sampleRate) {
    await (NativeModules.JuniperAudio as NativeAudio).openPlayer(sampleRate);
  },
  play(base64) {
    (NativeModules.JuniperAudio as NativeAudio | undefined)?.play(base64);
  },
  flush() {
    (NativeModules.JuniperAudio as NativeAudio | undefined)?.flush();
  },
  async closePlayer() {
    await (
      NativeModules.JuniperAudio as NativeAudio | undefined
    )?.closePlayer();
  },
};
