/**
 * Where the device credential lives (ADR-033 section 2): encrypted with `safeStorage` (DPAPI on
 * Windows) in the user data folder. If the system cannot encrypt, nothing is saved and login is
 * refused: it never falls back to plain text. DPAPI protects against another user and against
 * copying the file to another machine, not against malware running as the same user (accepted).
 */
import fs from 'node:fs/promises';
import type { Credential } from '../core/connection/credential';

export interface Crypto {
  isAvailable(): boolean;
  encrypt(text: string): Buffer;
  decrypt(data: Buffer): string;
}

export interface StoredLogin extends Credential {
  url: string;
  deviceId: string;
}

export class CredentialStore {
  constructor(
    private readonly file: string,
    private readonly crypto: Crypto,
  ) {}

  get secure(): boolean {
    return this.crypto.isAvailable();
  }

  async load(): Promise<StoredLogin | null> {
    if (!this.crypto.isAvailable()) return null;
    try {
      const data = JSON.parse(this.crypto.decrypt(await fs.readFile(this.file))) as StoredLogin;
      return data.url && data.clientId && data.clientSecret && data.deviceId ? data : null;
    } catch {
      return null;
    }
  }

  async save(login: StoredLogin): Promise<boolean> {
    if (!this.crypto.isAvailable()) return false;
    await fs.writeFile(this.file, this.crypto.encrypt(JSON.stringify(login)), { mode: 0o600 });
    return true;
  }

  async clear(): Promise<void> {
    await fs.rm(this.file, { force: true });
  }
}
