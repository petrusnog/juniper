import React from 'react';
import ReactTestRenderer from 'react-test-renderer';
import { Text } from 'react-native';
import { httpBase } from '../src/storage/credentials';
import { UpdateBanner, UpdateSettings } from '../src/ui/UpdateViews';
import type { InstallStatus, UpdateNative } from '../src/update/nativeUpdate';
import {
  parsePublished,
  UpdateController,
} from '../src/update/UpdateController';

const SHA = 'a'.repeat(64);
const PUBLISHED = {
  available: true,
  version_code: 12,
  version_name: '0.1.12',
  sha256: SHA,
  size: 4096,
  released_at: '2026-10-29T12:00:00Z',
};

class FakeNative implements UpdateNative {
  installed = { versionCode: 10, versionName: '0.1.10' };
  allowed = true;
  settingsOpened = 0;
  installs: [string, string, string, number][] = [];
  installError: { code: string } | null = null;
  progress: ((r: number, t: number) => void) | null = null;
  status: ((s: InstallStatus, m: string) => void) | null = null;
  missing = false;
  async installedVersion() {
    if (this.missing) {
      throw new Error('native_module_missing');
    }
    return this.installed;
  }
  async canInstall() {
    return this.allowed;
  }
  async openInstallSettings() {
    this.settingsOpened += 1;
  }
  async downloadAndInstall(
    url: string,
    token: string,
    sha256: string,
    size: number,
  ) {
    this.installs.push([url, token, sha256, size]);
    if (this.installError) {
      throw this.installError;
    }
  }
  onProgress(l: (r: number, t: number) => void) {
    this.progress = l;
    return () => {
      this.progress = null;
    };
  }
  onStatus(l: (s: InstallStatus, m: string) => void) {
    this.status = l;
    return () => {
      this.status = null;
    };
  }
}

function answer(status: number, body: unknown) {
  return jest.fn(async (_url: string, _init: unknown) => ({
    status,
    json: async () => body,
  }));
}

function make(
  fetchFn = answer(200, PUBLISHED),
  native = new FakeNative(),
): {
  controller: UpdateController;
  native: FakeNative;
  fetchFn: typeof fetchFn;
} {
  const controller = new UpdateController(
    { base: 'http://100.64.0.1:8765', token: 'segredo-123' },
    native,
    fetchFn as never,
  );
  return { controller, native, fetchFn };
}

test('the brain address is http on the same port as the websocket', () => {
  expect(httpBase({ host: ' 100.64.0.1 ', port: 8765 })).toBe(
    'http://100.64.0.1:8765',
  );
});

test('asks the brain with the token and offers a newer version', async () => {
  const { controller, fetchFn } = make();
  await controller.check();
  const [url, init] = fetchFn.mock.calls[0] as [
    string,
    { headers: Record<string, string> },
  ];
  expect(url).toBe('http://100.64.0.1:8765/app/update');
  expect(init.headers.Authorization).toBe('Bearer segredo-123');
  expect(controller.state.phase).toBe('available');
  expect(controller.state.installed).toEqual({ code: 10, name: '0.1.10' });
  expect(controller.state.available?.versionName).toBe('0.1.12');
});

test('the same or an older published version means up to date', async () => {
  for (const code of [10, 3]) {
    const { controller } = make(
      answer(200, { ...PUBLISHED, version_code: code }),
    );
    await controller.check();
    expect(controller.state.phase).toBe('current');
    expect(controller.state.available).toBeNull();
  }
});

test('nothing published is "up to date", not an error', async () => {
  const { controller } = make(answer(200, { available: false }));
  await controller.check();
  expect(controller.state.phase).toBe('current');
  expect(controller.state.notice).toBeNull();
});

test('a manual check explains each failure in plain words', async () => {
  const cases: [ReturnType<typeof answer>, string][] = [
    [answer(401, {}), 'recusou o token'],
    [answer(500, {}), 'inesperado'],
    [answer(200, { available: true, version_code: 'x' }), 'inesperado'],
  ];
  for (const [fetchFn, text] of cases) {
    const { controller } = make(fetchFn);
    await controller.check();
    expect(controller.state.phase).toBe('error');
    expect(controller.state.notice).toContain(text);
  }
  const offline = make(
    jest.fn(async () => {
      throw new Error('Network request failed');
    }) as never,
  );
  await offline.controller.check();
  expect(offline.controller.state.notice).toContain('Tailscale');
  const old = new FakeNative();
  old.missing = true;
  const noModule = make(answer(200, PUBLISHED), old);
  await noModule.controller.check();
  expect(noModule.controller.state.notice).toContain('APK novo');
});

test('the automatic check stays quiet when something is wrong', async () => {
  const { controller } = make(answer(401, {}));
  await controller.check({ silent: true });
  expect(controller.state.phase).toBe('idle');
  expect(controller.state.notice).toBeNull();
});

test('a check already running is not started twice', async () => {
  const { controller, fetchFn } = make();
  await Promise.all([controller.check(), controller.check()]);
  expect(fetchFn).toHaveBeenCalledTimes(1);
});

test('install downloads from the brain with the checksum and waits for the system', async () => {
  const { controller, native } = make();
  await controller.check();
  await controller.install();
  expect(native.installs).toEqual([
    ['http://100.64.0.1:8765/app/update/apk', 'segredo-123', SHA, 4096],
  ]);
  expect(controller.state.phase).toBe('installing');
});

test('the download progress is shown as a fraction', async () => {
  const { controller, native } = make();
  await controller.check();
  const pending = controller.install();
  await Promise.resolve();
  await Promise.resolve();
  native.progress?.(1024, 4096);
  expect(controller.state.progress).toBeCloseTo(0.25);
  await pending;
});

test('without the "install apps" permission it opens the settings and asks to try again', async () => {
  const { controller, native } = make();
  native.allowed = false;
  await controller.check();
  await controller.install();
  expect(native.settingsOpened).toBe(1);
  expect(native.installs).toEqual([]);
  expect(controller.state.phase).toBe('available');
  expect(controller.state.notice).toContain('instale apps');
});

test('a bad download or checksum goes back to "available" with the reason', async () => {
  const { controller, native } = make();
  await controller.check();
  native.installError = { code: 'checksum_mismatch' };
  await controller.install();
  expect(controller.state.phase).toBe('available');
  expect(controller.state.notice).toContain('não bate');
  native.installError = { code: 'whatever' };
  await controller.install();
  expect(controller.state.notice).toContain('baixar');
});

test('the system refusing the install is reported and can be retried', async () => {
  const { controller, native } = make();
  await controller.check();
  await controller.install();
  native.status?.('failed', 'INSTALL_FAILED_UPDATE_INCOMPATIBLE');
  expect(controller.state.phase).toBe('available');
  expect(controller.state.notice).toContain(
    'INSTALL_FAILED_UPDATE_INCOMPATIBLE',
  );
});

test('install does nothing before a version was found', async () => {
  const { controller, native } = make();
  await controller.install();
  expect(native.installs).toEqual([]);
});

test('parsePublished accepts only what the brain sends', () => {
  expect(parsePublished({ available: false })).toBeNull();
  expect(parsePublished(PUBLISHED)).toEqual({
    versionCode: 12,
    versionName: '0.1.12',
    sha256: SHA,
    size: 4096,
  });
  for (const bad of [
    null,
    'x',
    {},
    { ...PUBLISHED, sha256: 'xyz' },
    { ...PUBLISHED, size: 0 },
    { ...PUBLISHED, version_code: 1.5 },
    { ...PUBLISHED, version_name: 3 },
  ]) {
    expect(() => parsePublished(bad)).toThrow('bad_answer');
  }
});

// --- screens -----------------------------------------------------------------------------

function render(node: React.ReactElement) {
  let tree!: ReactTestRenderer.ReactTestRenderer;
  ReactTestRenderer.act(() => {
    tree = ReactTestRenderer.create(node);
  });
  const texts = () =>
    tree.root.findAllByType(Text).map(t => [t.props.children].flat().join(''));
  return { tree, texts };
}

test('the banner is hidden until a newer version is found, then installs on tap', async () => {
  const { controller, native } = make();
  const { tree, texts } = render(<UpdateBanner updater={controller} />);
  expect(tree.toJSON()).toBeNull();
  await ReactTestRenderer.act(async () => {
    await controller.check();
  });
  expect(texts()).toContain('Nova versão 0.1.12 disponível.');
  await ReactTestRenderer.act(async () => {
    await tree.root.findByProps({ testID: 'update-install' }).props.onPress();
  });
  expect(native.installs).toHaveLength(1);
});

test('the settings show the version and the manual check button', async () => {
  const { controller } = make();
  const { tree, texts } = render(<UpdateSettings updater={controller} />);
  expect(texts()).toContain('Versão instalada: toque em Buscar para ver.');
  await ReactTestRenderer.act(async () => {
    await tree.root.findByProps({ testID: 'update-check' }).props.onPress();
  });
  expect(texts()).toContain('Versão instalada: 0.1.10 (código 10)');
  expect(texts()).toContain('Nova versão 0.1.12 disponível.');
  expect(
    tree.root.findAllByProps({ testID: 'update-install-settings' }).length,
  ).toBeGreaterThan(0);
});
