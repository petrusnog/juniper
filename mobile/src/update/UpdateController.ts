/**
 * The app's self-update (ADR-026): asks the brain whether a newer APK is published, downloads it
 * and hands it to Android. The brain's answer is only a hint: the SHA-256 is checked while the
 * file is downloaded and Android refuses a package not signed with the installed app's key.
 */
import type { UpdateNative } from './nativeUpdate';

export type UpdatePhase =
  | 'idle'
  | 'checking'
  | 'current'
  | 'available'
  | 'downloading'
  | 'installing'
  | 'error';

export interface PublishedVersion {
  versionCode: number;
  versionName: string;
  sha256: string;
  size: number;
}

export interface UpdateState {
  phase: UpdatePhase;
  /** The installed version (`null` until the first check). */
  installed: { code: number; name: string } | null;
  available: PublishedVersion | null;
  /** 0 to 1 while downloading. */
  progress: number;
  notice: string | null;
}

export interface UpdateTarget {
  /** `http://host:port` of the brain. */
  base: string;
  token: string;
}

type Listener = () => void;
type FetchFn = (
  url: string,
  init: { headers: Record<string, string>; signal?: AbortSignal },
) => Promise<{ status: number; json(): Promise<unknown> }>;

export const CHECK_TIMEOUT_MS = 8000;
const SHA256 = /^[0-9a-f]{64}$/;
/** The same ceiling the brain applies (brain/clients/app_update.py). */
const MAX_APK_BYTES = 200 * 1024 * 1024;

const INSTALL_ERRORS: Record<string, string> = {
  install_permission:
    'Permita que a Juniper instale apps na tela que abriu e toque em Atualizar de novo.',
  checksum_mismatch:
    'O arquivo baixado não bate com o publicado. Tente de novo.',
  size_mismatch: 'O download veio incompleto. Tente de novo.',
  download_failed: 'Não consegui baixar a atualização. Confira a conexão.',
  install_failed: 'O Android recusou a instalação.',
};

export class UpdateController {
  state: UpdateState = {
    phase: 'idle',
    installed: null,
    available: null,
    progress: 0,
    notice: null,
  };
  private readonly listeners = new Set<Listener>();
  private offs: (() => void)[] = [];

  constructor(
    private readonly target: UpdateTarget,
    private readonly native: UpdateNative,
    private readonly fetchFn: FetchFn = (url, init) =>
      fetch(url, init) as ReturnType<FetchFn>,
  ) {}

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getState = (): UpdateState => this.state;

  private set(patch: Partial<UpdateState>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach(l => l());
  }

  private get busy(): boolean {
    const { phase } = this.state;
    return (
      phase === 'checking' || phase === 'downloading' || phase === 'installing'
    );
  }

  /**
   * Asks the brain for the newest published version. `silent` (the automatic check at startup)
   * does not show errors: a brain that is off or has nothing published is not news.
   */
  async check(options: { silent?: boolean } = {}): Promise<void> {
    if (this.busy) {
      return;
    }
    this.set({ phase: 'checking', notice: null });
    try {
      const installed = await this.native.installedVersion();
      const current = {
        code: installed.versionCode,
        name: installed.versionName,
      };
      const published = await this.fetchPublished();
      if (published !== null && published.versionCode > current.code) {
        this.set({
          phase: 'available',
          installed: current,
          available: published,
          notice: null,
        });
      } else {
        this.set({
          phase: 'current',
          installed: current,
          available: null,
          notice: null,
        });
      }
    } catch (error) {
      this.set({
        phase: options.silent ? 'idle' : 'error',
        notice: options.silent ? null : describeCheckError(error),
      });
    }
  }

  /** Downloads and installs the version found by `check`. */
  async install(): Promise<void> {
    const published = this.state.available;
    if (this.state.phase !== 'available' || published === null) {
      return;
    }
    try {
      if (!(await this.native.canInstall())) {
        await this.native.openInstallSettings();
        this.set({ notice: INSTALL_ERRORS.install_permission });
        return;
      }
      this.set({ phase: 'downloading', progress: 0, notice: null });
      this.offs = [
        this.native.onProgress((received, total) =>
          this.set({ progress: total > 0 ? received / total : 0 }),
        ),
        this.native.onStatus((status, message) =>
          this.onInstallStatus(status, message),
        ),
      ];
      await this.native.downloadAndInstall(
        `${this.target.base}/app/update/apk`,
        this.target.token,
        published.sha256,
        published.size,
      );
      this.set({
        phase: 'installing',
        progress: 1,
        notice: 'Confirme a instalação na tela do Android.',
      });
    } catch (error) {
      this.release();
      const { code = '', message = '' } = (error ?? {}) as {
        code?: string;
        message?: string;
      };
      // The short technical reason (e.g. SocketTimeoutException) helps to tell a cut connection
      // from a refusal.
      const detail = message && message !== code ? ` (${message})` : '';
      this.set({
        phase: 'available',
        notice: `${
          INSTALL_ERRORS[code] ?? INSTALL_ERRORS.download_failed
        }${detail}`,
      });
    }
  }

  /** Stops listening to the installer (the app is being closed or replaced). */
  dispose(): void {
    this.release();
    this.listeners.clear();
  }

  private release(): void {
    this.offs.forEach(off => off());
    this.offs = [];
  }

  private onInstallStatus(status: string, message: string): void {
    if (status === 'failed') {
      this.release();
      this.set({
        phase: 'available',
        notice: `${INSTALL_ERRORS.install_failed}${
          message ? ` (${message})` : ''
        }`,
      });
    } else if (status === 'success') {
      this.release();
      this.set({ phase: 'current', available: null, notice: null });
    }
  }

  private async fetchPublished(): Promise<PublishedVersion | null> {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), CHECK_TIMEOUT_MS);
    try {
      const response = await this.fetchFn(`${this.target.base}/app/update`, {
        headers: { Authorization: `Bearer ${this.target.token}` },
        signal: abort.signal,
      });
      if (response.status === 401) {
        throw new Error('unauthorized');
      }
      if (response.status !== 200) {
        throw new Error(`http_${response.status}`);
      }
      return parsePublished(await response.json());
    } finally {
      clearTimeout(timer);
    }
  }
}

/** `null` when nothing is published; throws on an answer that is not what the brain sends. */
export function parsePublished(body: unknown): PublishedVersion | null {
  const data = body as Record<string, unknown> | null;
  if (data === null || typeof data !== 'object') {
    throw new Error('bad_answer');
  }
  if (data.available === false) {
    return null;
  }
  const { version_code, version_name, sha256, size } = data;
  if (
    data.available !== true ||
    typeof version_code !== 'number' ||
    !Number.isInteger(version_code) ||
    version_code < 1 ||
    typeof version_name !== 'string' ||
    typeof sha256 !== 'string' ||
    !SHA256.test(sha256) ||
    typeof size !== 'number' ||
    !Number.isInteger(size) ||
    size < 1 ||
    size > MAX_APK_BYTES
  ) {
    throw new Error('bad_answer');
  }
  return { versionCode: version_code, versionName: version_name, sha256, size };
}

function describeCheckError(error: unknown): string {
  const text = error instanceof Error ? error.message : '';
  if (text === 'unauthorized') {
    return 'O servidor recusou o token. Confira em Configurar.';
  }
  if (text === 'native_module_missing') {
    return 'Este app não sabe se atualizar. Instale o APK novo uma vez.';
  }
  if (text.startsWith('http_') || text === 'bad_answer') {
    return 'O servidor respondeu algo inesperado sobre atualizações.';
  }
  return 'Não consegui falar com o servidor. Confira o Tailscale e o endereço.';
}
