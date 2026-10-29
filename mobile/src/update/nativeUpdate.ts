import { NativeEventEmitter, NativeModules } from 'react-native';

export interface InstalledVersion {
  versionCode: number;
  versionName: string;
}

export type InstallStatus = 'pending_user' | 'success' | 'failed';

/** What the app needs from Android to update itself (the Kotlin module `JuniperUpdate`). */
export interface UpdateNative {
  installedVersion(): Promise<InstalledVersion>;
  /** Whether Android lets the app install packages ("install unknown apps"). */
  canInstall(): Promise<boolean>;
  openInstallSettings(): Promise<void>;
  /** Downloads into an installer session, checks the SHA-256 and hands it to the system. */
  downloadAndInstall(
    url: string,
    token: string,
    sha256: string,
    size: number,
  ): Promise<void>;
  onProgress(listener: (received: number, total: number) => void): () => void;
  onStatus(
    listener: (status: InstallStatus, message: string) => void,
  ): () => void;
}

interface Module {
  installedVersion(): Promise<InstalledVersion>;
  canInstall(): Promise<boolean>;
  openInstallSettings(): Promise<void>;
  downloadAndInstall(
    url: string,
    token: string,
    sha256: string,
    size: number,
  ): Promise<void>;
}

function module_(): Module {
  const native = NativeModules.JuniperUpdate as Module | undefined;
  if (!native) {
    throw new Error('native_module_missing');
  }
  return native;
}

export const nativeUpdate: UpdateNative = {
  installedVersion: () => module_().installedVersion(),
  canInstall: () => module_().canInstall(),
  openInstallSettings: () => module_().openInstallSettings(),
  downloadAndInstall: (url, token, sha256, size) =>
    module_().downloadAndInstall(url, token, sha256, size),
  onProgress(listener) {
    const emitter = new NativeEventEmitter(NativeModules.JuniperUpdate);
    const sub = emitter.addListener('juniperUpdateProgress', (e: unknown) => {
      const { received, total } = e as { received: number; total: number };
      listener(received, total);
    });
    return () => sub.remove();
  },
  onStatus(listener) {
    const emitter = new NativeEventEmitter(NativeModules.JuniperUpdate);
    const sub = emitter.addListener('juniperUpdateStatus', (e: unknown) => {
      const { status, message } = e as {
        status: InstallStatus;
        message: string;
      };
      listener(status, message);
    });
    return () => sub.remove();
  },
};
