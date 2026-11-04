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
