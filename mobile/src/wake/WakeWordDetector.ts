/**
 * The wake word ("Juniper") detector, as an interface (ADR-023, wake word). The engine runs on
 * the phone, offline, only to spot the word: nothing is sent to the brain until it is heard.
 */
export interface WakeWordDetector {
  /** Whether it can run at all (the engine key is configured). */
  readonly available: boolean;
  /** Starts listening; `onWake` is called each time the word is heard. Releases nothing else. */
  start(onWake: () => void, onError: (message: string) => void): Promise<void>;
  /** Stops listening and gives the microphone back (the voice session needs it). */
  stop(): Promise<void>;
}
