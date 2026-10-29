/**
 * The hands-free flow (ADR-023, wake word), run by the app so the microphone is only ever open
 * when it should be:
 *
 *   standby (only the wake word detector listens; nothing goes to the brain)
 *     → the word is heard → the detector lets go of the microphone → a voice session opens
 *     → the brain greets (microphone closed) → the person asks → (microphone closed) the brain
 *       works, plays the music and comments → the brain ends the session
 *     → standby again. She only answers again after the wake word.
 *
 * A session that is not about music (no playback) stays open for follow-ups until the quiet
 * timeout (`voice.wake_idle_seconds`), then also goes back to standby.
 */
import type { BrainConnection } from '../connection/BrainConnection';
import type { AudioDevice } from '../voice/devices';
import type { VoiceController } from '../voice/VoiceController';
import type { WakeWordDetector } from './WakeWordDetector';

export type WakePhase = 'off' | 'standby' | 'session';

export interface WakeState {
  phase: WakePhase;
  notice: string | null;
}

const NOTIFICATION_TITLE = 'Juniper está esperando';
const NOTIFICATION_TEXT =
  'Diga «Juniper» para falar. Toque em Parar para desligar.';

type Listener = () => void;

export class WakeController {
  state: WakeState = { phase: 'off', notice: null };
  private readonly listeners = new Set<Listener>();
  private offs: (() => void)[] = [];
  private busy = false;

  constructor(
    private readonly connection: BrainConnection,
    private readonly voice: VoiceController,
    private readonly audio: AudioDevice,
    private readonly detector: WakeWordDetector,
  ) {}

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getState = (): WakeState => this.state;

  get available(): boolean {
    return this.detector.available;
  }

  private set(patch: Partial<WakeState>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach(l => l());
  }

  /** Turns the hands-free mode on. `false` (with a notice) if it could not. */
  async enable(): Promise<boolean> {
    if (this.state.phase !== 'off') {
      return true;
    }
    if (!this.detector.available) {
      this.set({
        notice:
          'Falta a chave da Picovoice para ouvir «Juniper». Coloque-a em Configurar.',
      });
      return false;
    }
    try {
      await this.audio.startService(NOTIFICATION_TITLE, NOTIFICATION_TEXT);
    } catch {
      this.set({
        notice:
          'Não consegui abrir o microfone em segundo plano. Confira as permissões.',
      });
      return false;
    }
    this.voice.keepService = true;
    this.offs = [
      this.voice.subscribe(() => this.onVoiceChange()),
      this.audio.onStopRequested(() => {
        if (this.state.phase === 'standby') {
          this.disable().catch(() => undefined);
        }
      }),
    ];
    this.set({ phase: 'standby', notice: null });
    return this.listen();
  }

  /** Turns it off: stops the detector, ends any session and drops the notification. */
  async disable(): Promise<void> {
    if (this.state.phase === 'off') {
      return;
    }
    this.offs.forEach(off => off());
    this.offs = [];
    this.set({ phase: 'off' });
    this.voice.keepService = false;
    await this.detector.stop().catch(() => undefined);
    await this.voice.end();
    await this.audio.stopService().catch(() => undefined);
  }

  private async listen(): Promise<boolean> {
    try {
      await this.detector.start(
        () => {
          this.onWake().catch(() => undefined);
        },
        message => this.failed(message),
      );
      return true;
    } catch {
      await this.failed('detector');
      return false;
    }
  }

  private async failed(reason: string): Promise<void> {
    const hint =
      reason === 'missing_access_key'
        ? 'Falta a chave da Picovoice.'
        : 'A palavra de ativação não pôde iniciar. Confira a chave da Picovoice e o arquivo juniper.ppn.';
    await this.disable();
    this.set({ notice: hint });
  }

  private async onWake(): Promise<void> {
    if (this.state.phase !== 'standby' || this.busy) {
      return;
    }
    this.busy = true;
    try {
      this.set({ phase: 'session' });
      await this.detector.stop().catch(() => undefined); // the session needs the microphone
      const opened = await this.voice.start({ wake: true });
      if (!opened) {
        await this.backToStandby();
      }
    } finally {
      this.busy = false;
    }
  }

  private onVoiceChange(): void {
    // The session closed by itself (the music started, silence, an error): wait for the word again.
    if (
      this.state.phase === 'session' &&
      this.voice.state.phase === 'off' &&
      !this.busy
    ) {
      this.backToStandby().catch(() => undefined);
    }
  }

  private async backToStandby(): Promise<void> {
    if (this.state.phase === 'off') {
      return;
    }
    this.set({ phase: 'standby' });
    await this.listen();
  }
}
