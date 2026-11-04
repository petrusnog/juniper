/**
 * The border with the operating system (ADR-033 section 1.1).
 *
 * Everything that depends on the OS and matters for security lives behind this interface. The
 * rest of the app never imports `node:os` nor looks at `process.platform`: it receives a
 * `PlatformRules`. The v1 has one implementation (`main/platform/win32.ts`); Linux or macOS
 * would be another implementation of the same interface, with the same hostile-path tests.
 */
export interface Resolved {
  /** The real path (links and junctions followed, casing as the OS reports it). */
  real: string;
  /** Some component of the path (not only the last) is a link, junction or reparse point. */
  viaLink: boolean;
  exists: boolean;
}

export interface PlatformRules {
  readonly name: string;
  readonly isCaseSensitive: boolean;
  homeDir(): string;
  /** Absolute directories (prefixes) that are never read nor written (the fixed list, ADR-033). */
  blockedPaths(): string[];
  /** Why this path text is refused before touching the disk, or `null` if its syntax is fine. */
  unsafeSyntax(path: string): string | null;
  /** The file name only: sensitive names (keys, vaults, `.env`...) are blocked anywhere. */
  isSensitiveName(name: string): boolean;
  /** Names that run code (executables, shortcuts, scripts): never a write destination. */
  isExecutableName(name: string): boolean;
  /** Folders whose contents run code later (`.git/hooks`, `.vscode`): never written. */
  isProtectedFolderName(name: string, parentName: string): boolean;
  /** Whether `child` is `parent` or inside it, by the OS's own comparison rules. */
  isInside(child: string, parent: string): boolean;
  /** Same path by the OS's comparison rules (case, normalisation). */
  samePath(a: string, b: string): boolean;
  /** Splits an absolute path into its components (after the root). */
  components(path: string): string[];
  /** Path operations by the OS's own rules (never the `path` of the machine running the code). */
  join(...parts: string[]): string;
  dirname(p: string): string;
  basename(p: string): string;
  extname(p: string): string;
  /** The OS's answer about the real path: never guessed from the text. */
  canonicalize(path: string): Promise<Resolved>;
}
