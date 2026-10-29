const store: { value: string | null } = { value: null };
jest.mock('react-native-keychain', () => ({
  ACCESSIBLE: { WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'u' },
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

jest.mock(
  'react-native-safe-area-context',
  () => require('react-native-safe-area-context/jest/mock').default,
);

// These trees are never unmounted, and the real brand mark would keep its blinking timer alive
// (the BrandMark tests cover it with fake timers).
jest.mock('../src/ui/BrandMark', () => ({ BrandMark: () => null }));

import React from 'react';
import ReactTestRenderer from 'react-test-renderer';
import { Text, TextInput } from 'react-native';
import App from '../App';
import { SetupScreen } from '../src/ui/SetupScreen';

function find(root: ReactTestRenderer.ReactTestInstance, testID: string) {
  return root.findByProps({ testID });
}
function textOf(root: ReactTestRenderer.ReactTestInstance): string {
  return root
    .findAllByType(Text)
    .map(t => t.props.children)
    .flat()
    .join(' ');
}

beforeEach(() => {
  store.value = null;
});

test('SetupScreen refuses a bad address and a short token, and shows why', () => {
  const onSave = jest.fn();
  let tree!: ReactTestRenderer.ReactTestRenderer;
  ReactTestRenderer.act(() => {
    tree = ReactTestRenderer.create(<SetupScreen onSave={onSave} />);
  });
  const root = tree.root;
  ReactTestRenderer.act(() =>
    find(root, 'host').props.onChangeText('http://x'),
  );
  ReactTestRenderer.act(() => find(root, 'save').props.onPress());
  expect(textOf(root)).toContain('Endereço inválido');
  ReactTestRenderer.act(() =>
    find(root, 'host').props.onChangeText('100.64.0.7'),
  );
  ReactTestRenderer.act(() => find(root, 'token').props.onChangeText('curto'));
  ReactTestRenderer.act(() => find(root, 'save').props.onPress());
  expect(textOf(root)).toContain('token completo');
  expect(onSave).not.toHaveBeenCalled();
});

test('SetupScreen saves trimmed values with a device id', () => {
  const onSave = jest.fn();
  let tree!: ReactTestRenderer.ReactTestRenderer;
  ReactTestRenderer.act(() => {
    tree = ReactTestRenderer.create(<SetupScreen onSave={onSave} />);
  });
  const root = tree.root;
  ReactTestRenderer.act(() =>
    find(root, 'host').props.onChangeText(' 100.64.0.7 '),
  );
  ReactTestRenderer.act(() =>
    find(root, 'token').props.onChangeText(' token-bem-longo '),
  );
  ReactTestRenderer.act(() => find(root, 'save').props.onPress());
  expect(onSave).toHaveBeenCalledWith(
    expect.objectContaining({
      host: '100.64.0.7',
      port: 8765,
      token: 'token-bem-longo',
      deviceName: 'Celular',
      deviceId: expect.stringMatching(/^mobile-/),
    }),
  );
});

test('the token field hides what is typed', () => {
  let tree!: ReactTestRenderer.ReactTestRenderer;
  ReactTestRenderer.act(() => {
    tree = ReactTestRenderer.create(<SetupScreen onSave={jest.fn()} />);
  });
  const inputs = tree.root.findAllByType(TextInput);
  expect(
    inputs.find(i => i.props.testID === 'token')?.props.secureTextEntry,
  ).toBe(true);
});

test('the app asks for the setup when nothing is stored', async () => {
  let tree!: ReactTestRenderer.ReactTestRenderer;
  await ReactTestRenderer.act(async () => {
    tree = ReactTestRenderer.create(<App />);
  });
  await ReactTestRenderer.act(async () => {
    await new Promise(resolve => setImmediate(resolve));
  });
  expect(textOf(tree.root)).toContain('Conectar à Juniper');
});
