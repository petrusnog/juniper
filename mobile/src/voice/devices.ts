/**
 * What the voice chat needs from the phone, as interfaces (ADR-022, section 5). The Kotlin
 * modules and the system speech engines implement them; the controller and its tests only see
 * these, so the whole voice flow is testable without a device.
 */

/** Microphone capture (with the platform echo cancellation) and playback of her voice. */
export interface AudioDevice {
  /** Starts the foreground service and the microphone; PCM 16-bit mono at `sampleRate`. */
  start(sampleRate: number): Promise<void>;
  /** Ends the capture; `keepService` leaves the foreground service up (the wake word is waiting). */
  stop(keepService?: boolean): Promise<void>;
  /** The foreground service alone, with its notification (the phone waiting for the wake word). */
  startService(title: string, text: string): Promise<void>;
  stopService(): Promise<void>;
  /** Chunks of about 100 ms, base64 PCM. Returns the unsubscribe function. */
  onChunk(listener: (base64Pcm: string) => void): () => void;
  /** The person pressed "encerrar" in the notification. */
  onStopRequested(listener: () => void): () => void;
  /** Releases the microphone without ending the session (the device recognizer needs it). */
  pauseCapture(): Promise<void>;
  resumeCapture(): Promise<void>;
  /** Whether headphones (wired or Bluetooth) are the way out right now. */
  hasHeadset(): Promise<boolean>;
  /** Loudspeaker (`true`) or whatever is plugged in (`false`); also turns on echo cancellation. */
  applyRoute(speaker: boolean): Promise<void>;
  /** Headphones plugged in or out. Returns the unsubscribe function. */
  onRouteChange(listener: (headset: boolean) => void): () => void;
  openPlayer(sampleRate: number): Promise<void>;
  /** Queues PCM for playback, after what is already queued. */
  play(base64Pcm: string): void;
  /** Drops everything queued and stops at once (she was cut off). */
  flush(): void;
  closePlayer(): Promise<void>;
}

/** The Android `TextToSpeech`: the fallback when the cloud voice is gone (ADR-023, section 4). */
export interface DeviceTts {
  readonly available: boolean;
  speak(text: string): void;
  stop(): void;
}

/** The Android speech recognizer: the fallback when the cloud STT is gone (ADR-023, section 5). */
export interface DeviceStt {
  readonly available: boolean;
  /** Listens (pt-BR) and calls `onText` with each final utterance until `stop`. */
  start(
    onText: (text: string) => void,
    onError: (message: string) => void,
  ): Promise<void>;
  stop(): Promise<void>;
}
