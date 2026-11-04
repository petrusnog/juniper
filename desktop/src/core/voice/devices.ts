/**
 * What the voice chat needs from the computer, as interfaces (ADR-033, ADR-023). The window
 * (microphone, speakers) implements `AudioDevice` through the bridge; the controller and its
 * tests only see these, so the whole voice flow is testable without a sound card.
 *
 * The desktop has no system speech engines to fall back to: `DeviceTts` and `DeviceStt` exist so
 * the controller is the same as the phone's, and both answer `available = false`.
 */
export interface AudioDevice {
  /** Opens the microphone (echo cancellation and noise suppression on); PCM 16-bit mono. */
  start(sampleRate: number): Promise<void>;
  stop(): Promise<void>;
  /** Chunks of about 100 ms, base64 PCM. Returns the unsubscribe function. */
  onChunk(listener: (base64Pcm: string) => void): () => void;
  /** The person asked to end the conversation from the window. */
  onStopRequested(listener: () => void): () => void;
  pauseCapture(): Promise<void>;
  resumeCapture(): Promise<void>;
  /** Whether headphones are the way out (the person says so: the desktop cannot tell). */
  hasHeadset(): Promise<boolean>;
  /** Loudspeaker (`true`) or headphones (`false`). */
  applyRoute(speaker: boolean): Promise<void>;
  onRouteChange(listener: (headset: boolean) => void): () => void;
  openPlayer(sampleRate: number): Promise<void>;
  /** Queues PCM for playback, after what is already queued. */
  play(base64Pcm: string): void;
  /** Drops everything queued and stops at once (she was cut off). */
  flush(): void;
  closePlayer(): Promise<void>;
}

export interface DeviceTts {
  readonly available: boolean;
  speak(text: string): void;
  stop(): void;
}

export interface DeviceStt {
  readonly available: boolean;
  start(onText: (text: string) => void, onError: (message: string) => void): Promise<void>;
  stop(): Promise<void>;
}

export const noTts: DeviceTts = { available: false, speak: () => undefined, stop: () => undefined };
export const noStt: DeviceStt = {
  available: false,
  start: async () => undefined,
  stop: async () => undefined,
};
