/**
 * Self-update of the desktop app (ADR-033, in the mould of ADR-026).
 *
 * The brain serves the installer over the private network; THIS code decides whether to run it.
 * Nothing is executed unless, in this order: the size and the SHA-256 match what the brain said,
 * and the file carries a valid Authenticode signature by the publisher this build was made for.
 * A build without a publisher never updates itself. The person starts the update (a button): it
 * never installs on its own. Pure: the network, the disk, PowerShell and the installer are ports.
 */
export interface UpdateInfo {
  available: boolean;
  version_code?: number;
  version_name?: string;
  sha256?: string;
  size?: number;
}

export interface UpdatePorts {
  /** `GET /desktop/update` with the credential; `null` if the brain could not be asked. */
  fetchInfo(): Promise<UpdateInfo | null>;
  /** Downloads the installer to a temporary file; throws if it exceeds `maxBytes`. Returns the path. */
  download(maxBytes: number): Promise<string>;
  fileSize(path: string): Promise<number>;
  sha256(path: string): Promise<string>;
  /**
   * Authenticode: the file's product version if its signature is valid and its signer is
   * `publisher`, otherwise `null`. The version is read from the SIGNED file itself.
   */
  signedVersion(path: string, publisher: string): Promise<string | null>;
  remove(path: string): Promise<void>;
  /** Starts the installer and closes the app. */
  install(path: string): Promise<void>;
}

export type UpdateState =
  | { phase: 'none' }
  | { phase: 'available'; version: string }
  | { phase: 'working'; version: string; step: 'downloading' | 'verifying' | 'installing' }
  | { phase: 'error'; message: string };

const MAX_INSTALLER_BYTES = 300 * 1024 * 1024;
const SHA256 = /^[0-9a-f]{64}$/;

/** The build number of an app version like `0.1.57` is its last component (the commit count). */
export function codeOf(version: string): number {
  const last = version.split('.').pop() ?? '';
  const n = Number.parseInt(last, 10);
  return Number.isFinite(n) ? n : 0;
}

export class UpdateController {
  state: UpdateState = { phase: 'none' };
  private info: Required<Pick<UpdateInfo, 'version_code' | 'version_name' | 'sha256' | 'size'>> | null =
    null;
  private readonly listeners = new Set<() => void>();

  constructor(
    private readonly ports: UpdatePorts,
    private readonly installedVersion: string,
    /** The signer this build trusts (from the build); empty means "unsigned build". */
    private readonly publisher: string,
  ) {}

  subscribe = (l: () => void): (() => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };

  private set(state: UpdateState): void {
    this.state = state;
    this.listeners.forEach(l => l());
  }

  /** Asks the brain whether a newer build exists. Never throws. */
  async check(): Promise<void> {
    if (this.state.phase === 'working') return;
    let info: UpdateInfo | null;
    try {
      info = await this.ports.fetchInfo();
    } catch {
      info = null;
    }
    if (
      info === null ||
      !info.available ||
      typeof info.version_code !== 'number' ||
      typeof info.version_name !== 'string' ||
      typeof info.size !== 'number' ||
      typeof info.sha256 !== 'string' ||
      !SHA256.test(info.sha256) ||
      info.size <= 0 ||
      info.size > MAX_INSTALLER_BYTES ||
      info.version_code <= codeOf(this.installedVersion)
    ) {
      this.info = null;
      if (this.state.phase !== 'error') this.set({ phase: 'none' });
      return;
    }
    this.info = {
      version_code: info.version_code,
      version_name: info.version_name,
      sha256: info.sha256,
      size: info.size,
    };
    this.set({ phase: 'available', version: info.version_name });
  }

  /** The person pressed "update". Downloads, verifies and only then starts the installer. */
  async apply(): Promise<void> {
    const info = this.info;
    if (this.state.phase !== 'available' || info === null) return;
    if (this.publisher === '') {
      return this.fail('Esta versão do app não tem assinatura, então não se atualiza sozinha.');
    }
    const version = info.version_name;
    let file: string | null = null;
    try {
      this.set({ phase: 'working', version, step: 'downloading' });
      file = await this.ports.download(info.size);
      this.set({ phase: 'working', version, step: 'verifying' });
      if ((await this.ports.fileSize(file)) !== info.size) {
        throw new Refusal('O instalador baixado tem tamanho diferente do anunciado.');
      }
      if ((await this.ports.sha256(file)).toLowerCase() !== info.sha256) {
        throw new Refusal('O instalador baixado não confere com o SHA-256 anunciado.');
      }
      const signed = await this.ports.signedVersion(file, this.publisher);
      if (signed === null) {
        throw new Refusal('O instalador não tem uma assinatura válida da Juniper. Não instalei.');
      }
      // The announced version must be the one inside the signed file, and newer than this one:
      // an old, legitimately signed installer cannot be passed off as a new release.
      if (signed !== info.version_name || codeOf(signed) <= codeOf(this.installedVersion)) {
        throw new Refusal('O instalador assinado não é da versão anunciada. Não instalei.');
      }
      // The file sat on disk while PowerShell looked at it: hash it again right before running.
      if ((await this.ports.sha256(file)).toLowerCase() !== info.sha256) {
        throw new Refusal('O instalador mudou depois de conferido. Não instalei.');
      }
      this.set({ phase: 'working', version, step: 'installing' });
      await this.ports.install(file);
    } catch (e) {
      if (file !== null) await this.ports.remove(file).catch(() => undefined);
      this.fail(e instanceof Refusal ? e.message : 'Não consegui baixar ou instalar a atualização.');
    }
  }

  private fail(message: string): void {
    this.info = null;
    this.set({ phase: 'error', message });
  }
}

class Refusal extends Error {}

/**
 * Reads the JSON that the PowerShell snippet prints and returns the file's product version if the
 * signature is valid and the signer's simple name (the certificate's CN, as Windows reports it) is
 * exactly the publisher. No string parsing of a certificate subject.
 */
export function trustedVersion(output: string, publisher: string): string | null {
  if (publisher === '') return null;
  let data: { status?: unknown; name?: unknown; version?: unknown };
  try {
    data = JSON.parse(output.trim()) as typeof data;
  } catch {
    return null;
  }
  if (
    data.status !== 'Valid' ||
    typeof data.name !== 'string' ||
    typeof data.version !== 'string' ||
    data.name.trim().toLowerCase() !== publisher.toLowerCase()
  ) {
    return null;
  }
  return data.version.trim();
}
