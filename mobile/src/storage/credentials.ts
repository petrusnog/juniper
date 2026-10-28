/**
 * Where the server address and the token live: the Android Keystore, through Keychain
 * (ADR-022, section 3). The token is never written anywhere else and never logged.
 */
import * as Keychain from 'react-native-keychain';

export interface Credentials {
  host: string;
  port: number;
  token: string;
  deviceId: string;
  deviceName: string;
}

const SERVICE = 'app.juniper.mobile.brain';

export function validateAddress(host: string, port: number): string | null {
  const clean = host.trim();
  if (
    !/^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$/.test(clean) &&
    !/^\[[0-9a-fA-F:]+\]$/.test(clean)
  ) {
    return 'Endereço inválido. Use o nome ou o IP do Tailscale, sem http:// nem porta.';
  }
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    return 'Porta inválida (1 a 65535).';
  }
  return null;
}

export function serverUrl(c: Pick<Credentials, 'host' | 'port'>): string {
  return `ws://${c.host.trim()}:${c.port}/v1/ws`;
}

export function newDeviceId(random: () => number = Math.random): string {
  const part = () =>
    Math.floor(random() * 0x100000000)
      .toString(16)
      .padStart(8, '0');
  return `mobile-${part()}${part()}`;
}

export async function loadCredentials(): Promise<Credentials | null> {
  try {
    const found = await Keychain.getGenericPassword({ service: SERVICE });
    if (!found) {
      return null;
    }
    const data = JSON.parse(found.password) as Partial<Credentials>;
    if (!data.host || !data.port || !data.token || !data.deviceId) {
      return null;
    }
    return {
      host: data.host,
      port: data.port,
      token: data.token,
      deviceId: data.deviceId,
      deviceName: data.deviceName ?? 'Celular',
    };
  } catch {
    return null; // unreadable store: the app asks for the setup again
  }
}

export async function saveCredentials(c: Credentials): Promise<void> {
  await Keychain.setGenericPassword('juniper', JSON.stringify(c), {
    service: SERVICE,
    accessible: Keychain.ACCESSIBLE.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
  });
}

export async function clearCredentials(): Promise<void> {
  await Keychain.resetGenericPassword({ service: SERVICE });
}
