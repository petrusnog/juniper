/** Commands the main process sends to the window's audio engine, and what the window reports. */
export type AudioCommand =
  | { kind: 'capture-start'; id: number; rate: number }
  | { kind: 'capture-stop'; id: number }
  | { kind: 'capture-pause' }
  | { kind: 'capture-resume' }
  | { kind: 'player-open'; id: number; rate: number }
  | { kind: 'play'; data: string }
  | { kind: 'flush' }
  | { kind: 'player-close'; id: number };

export type AudioEvent =
  | { kind: 'ack'; id: number; ok: boolean; error?: string }
  | { kind: 'chunk'; data: string }
  | { kind: 'stop-requested' }
  | { kind: 'headset'; value: boolean };

const MAX_CHUNK_CHARS = 64 * 1024; // 100 ms of 16 kHz PCM16 is about 4 KB of base64

/** What the main process accepts from the window: anything else is dropped. */
export function parseAudioEvent(value: unknown): AudioEvent | null {
  if (typeof value !== 'object' || value === null) return null;
  const e = value as Record<string, unknown>;
  switch (e.kind) {
    case 'ack':
      return Number.isInteger(e.id) && typeof e.ok === 'boolean'
        ? {
            kind: 'ack',
            id: e.id as number,
            ok: e.ok,
            ...(typeof e.error === 'string' ? { error: e.error.slice(0, 80) } : {}),
          }
        : null;
    case 'chunk':
      return typeof e.data === 'string' && e.data.length <= MAX_CHUNK_CHARS
        ? { kind: 'chunk', data: e.data }
        : null;
    case 'stop-requested':
      return { kind: 'stop-requested' };
    case 'headset':
      return typeof e.value === 'boolean' ? { kind: 'headset', value: e.value } : null;
    default:
      return null;
  }
}
