/**
 * The wake word ("Juniper") detector, as an interface. Not wired in yet: the wake word is in the
 * backlog (an on-device detector such as openWakeWord, with a model trained for "Juniper").
 * Until then the voice opens through `VoiceLauncher` (notification, shortcut, assistant link).
 */
export interface WakeWordDetector {
  /** Whether it can run at all (the engine and its model are present). */
  readonly available: boolean;
  /** Starts listening; `onWake` is called each time the word is heard. */
  start(onWake: () => void, onError: (message: string) => void): Promise<void>;
  /** Stops listening and gives the microphone back (the voice session needs it). */
  stop(): Promise<void>;
}
