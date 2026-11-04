/**
 * What the build baked in. `__PUBLISHER__` is the exact Common Name of the code-signing
 * certificate the release workflow signs with (JUNIPER_PUBLISHER at build time). A development
 * build has none, and an app without it never updates itself (ADR-033).
 */
declare const __PUBLISHER__: string | undefined;

export const PUBLISHER: string = typeof __PUBLISHER__ === 'string' ? __PUBLISHER__ : '';
