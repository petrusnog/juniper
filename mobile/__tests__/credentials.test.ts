const store: { value: string | null } = { value: null };
jest.mock('react-native-keychain', () => ({
  ACCESSIBLE: { WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'unlocked-this-device' },
  getGenericPassword: jest.fn(async () =>
    store.value === null
      ? false
      : { username: 'juniper', password: store.value },
  ),
  setGenericPassword: jest.fn(async (_u: string, p: string) => {
    store.value = p;
  }),
  resetGenericPassword: jest.fn(async () => {
    store.value = null;
  }),
}));

import * as Keychain from 'react-native-keychain';
import {
  clearCredentials,
  loadCredentials,
  newDeviceId,
  saveCredentials,
  serverUrl,
  validateAddress,
} from '../src/storage/credentials';

const creds = {
  host: '100.64.0.7',
  port: 8765,
  token: 'tok',
  deviceId: 'mobile-1',
  deviceName: 'Pixel',
};

beforeEach(() => {
  store.value = null;
});

test('saves to the keystore only for this device and loads it back', async () => {
  await saveCredentials(creds);
  expect(Keychain.setGenericPassword).toHaveBeenCalledWith(
    'juniper',
    expect.any(String),
    expect.objectContaining({ accessible: 'unlocked-this-device' }),
  );
  expect(await loadCredentials()).toEqual(creds);
});

test('nothing stored, a corrupt value or an incomplete one means "ask again"', async () => {
  expect(await loadCredentials()).toBeNull();
  store.value = 'not json';
  expect(await loadCredentials()).toBeNull();
  store.value = JSON.stringify({ host: 'x' });
  expect(await loadCredentials()).toBeNull();
});

test('clear forgets the credentials', async () => {
  await saveCredentials(creds);
  await clearCredentials();
  expect(await loadCredentials()).toBeNull();
});

test.each([
  ['100.64.0.7', 8765, null],
  ['juniper.tailnet.ts.net', 8765, null],
  ['[fd7a:115c::1]', 8765, null],
  ['http://100.64.0.7', 8765, 'Endereço inválido'],
  ['100.64.0.7:8765', 8765, 'Endereço inválido'],
  ['', 8765, 'Endereço inválido'],
  ['a b', 8765, 'Endereço inválido'],
  ['host/..', 8765, 'Endereço inválido'],
  ['100.64.0.7', 0, 'Porta inválida'],
  ['100.64.0.7', 70000, 'Porta inválida'],
  ['100.64.0.7', 80.5, 'Porta inválida'],
])('validates the address %s:%d', (host, port, error) => {
  const result = validateAddress(host, port);
  if (error === null) {
    expect(result).toBeNull();
  } else {
    expect(result).toContain(error);
  }
});

test('builds the websocket url', () => {
  expect(serverUrl({ host: ' 100.64.0.7 ', port: 8765 })).toBe(
    'ws://100.64.0.7:8765/v1/ws',
  );
});

test('device ids are stable-looking and differ', () => {
  expect(newDeviceId()).toMatch(/^mobile-[0-9a-f]{16}$/);
  expect(newDeviceId()).not.toBe(newDeviceId());
});
