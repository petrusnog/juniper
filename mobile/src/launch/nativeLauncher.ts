import { NativeModules, PermissionsAndroid, Platform } from 'react-native';

/** The ongoing notification with a «Falar» button that opens the voice (`juniper://voz`). */
export interface LauncherNotification {
  show(): Promise<boolean>;
  hide(): Promise<void>;
}

interface NativeLauncher {
  show(): Promise<void>;
  hide(): Promise<void>;
}

/** The Kotlin module `JuniperLauncher` (android/.../voice/JuniperLauncherModule.kt). */
export const nativeLauncher: LauncherNotification = {
  async show() {
    const native = NativeModules.JuniperLauncher as NativeLauncher | undefined;
    if (!native) {
      return false;
    }
    if (Platform.OS === 'android' && Number(Platform.Version) >= 33) {
      const granted = await PermissionsAndroid.request(
        PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS,
      );
      if (granted !== PermissionsAndroid.RESULTS.GRANTED) {
        return false;
      }
    }
    await native.show();
    return true;
  },
  async hide() {
    await (NativeModules.JuniperLauncher as NativeLauncher | undefined)?.hide();
  },
};
