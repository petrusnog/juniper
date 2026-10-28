import { NativeModules } from 'react-native';
import { DeviceTts } from './devices';

interface NativeTts {
  isAvailable(): Promise<boolean>;
  speak(text: string): void;
  stop(): void;
}

/**
 * The Android text-to-speech. `available` becomes true once the engine says a pt-BR voice is
 * installed (call `probeDeviceTts` at startup); until then `voice.start` says the device cannot
 * speak and the brain keeps the caption only.
 */
class DeviceTtsImpl implements DeviceTts {
  available = false;
  private get native(): NativeTts | undefined {
    return NativeModules.JuniperTts as NativeTts | undefined;
  }
  async probe(): Promise<void> {
    try {
      this.available = (await this.native?.isAvailable()) === true;
    } catch {
      this.available = false;
    }
  }
  speak(text: string): void {
    if (this.available) {
      this.native?.speak(text);
    }
  }
  stop(): void {
    this.native?.stop();
  }
}

export const deviceTts = new DeviceTtsImpl();
