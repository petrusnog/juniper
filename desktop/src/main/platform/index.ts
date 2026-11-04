/**
 * Chooses the `PlatformRules` implementation for this system. This is the ONLY place that looks
 * at `process.platform` (lint enforces it). The v1 supports Windows only (ADR-033).
 */
import os from 'node:os';
import type { PlatformRules } from '../../core/platform';
import { createWin32Rules } from './win32';

export function currentPlatform(appDataDir?: string): PlatformRules {
  if (process.platform !== 'win32') {
    throw new Error(`Sistema não suportado na v1: ${process.platform}`);
  }
  return createWin32Rules({
    home: os.homedir(),
    ...(appDataDir ? { appDataDir } : {}),
  });
}

/** The name this computer shows to the brain (`device_name`). */
export function deviceName(): string {
  return os.hostname();
}
