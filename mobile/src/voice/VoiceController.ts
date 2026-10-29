/* eslint-disable no-void -- fire-and-forget calls that handle their own errors */
/**
 * The voice chat session on top of a `BrainConnection` (ADR-022 section 5, ADR-023).
 *
 * The brain decides where an utterance ends, transcribes it and talks back; this only streams the
 * microphone, plays what comes back and shows the state. Nothing here interprets what was said
 * (not even "tchau": the brain recognises the commands, never the app or the LLM).
 */
import type { BrainConnection } from '../connection/BrainConnection';
import { isType } from '../protocol';
import type { AnyMessage, Msg } from '../protocol';
import { AudioDevice, DeviceStt, DeviceTts } from './devices';

export const INPUT_RATE = 16_000;

export type VoicePhase =
  | 'off'
  | 'starting'
  | 'listening'
  | 'thinking'
  | 'speaking';
export type Mode = 'cloud' | 'local';

export interface VoiceState {
  phase: VoicePhase;
  stt: Mode;
  tts: Mode;
  /** What the brain understood the person said (the last utterance). */
  userText: string;
  /** Where her voice plays: the loudspeaker (the microphone hears her) or headphones. */
  route: 'speaker' | 'headset';
  /** "Hold to talk": the microphone only goes to the brain while the button is held. */
  pushToTalk: boolean;
  /** The talk button is being held right now (only meaningful with `pushToTalk`). */
  talking: boolean;
  /** Why the session ended or could not start (PT-BR, for the screen). */
  notice: string | null;
}

export const initialVoice: VoiceState = {
  phase: 'off',
  stt: 'cloud',
  tts: 'cloud',
  userText: '',
  route: 'speaker',
  pushToTalk: false,
  talking: false,
  notice: null,
};

/** 100 ms of silence (16 kHz, 16 bits): 3200 zero bytes in base64. */
const SILENCE_CHUNK = `${'A'.repeat(4267)}=`;
/** What follows a released talk button, so the brain's detector hears the utterance end. */
const SILENCE_CHUNKS_AFTER_TALK = 9;

const END_TEXT: Record<string, string | null> = {
  client: null,
  command: 'Conversa de voz encerrada.',
  silence: 'Conversa de voz encerrada por silêncio.',
  error: 'A conversa de voz foi encerrada por um erro.',
  server: 'O servidor encerrou a conversa de voz.',
  playback: null, // the music started: the conversation closes on purpose, quietly
};

type Listener = () => void;

export class VoiceController {
  state: VoiceState = initialVoice;
  private readonly listeners = new Set<Listener>();
  private seq = 0;
  private sessionOpen = false;
  private readonly cut = new Set<string>();
  private offs: (() => void)[] = [];
  private starting = false;

  constructor(
    private readonly connection: BrainConnection,
    private readonly audio: AudioDevice,
    private readonly tts: DeviceTts,
    private readonly stt: DeviceStt,
    private readonly newId: () => string = () => `v-${Date.now().toString(36)}`,
  ) {}

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getState = (): VoiceState => this.state;

  get active(): boolean {
    return this.state.phase !== 'off';
  }

  private set(patch: Partial<VoiceState>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach(l => l());
  }

  /** Opens the session. `false` (with a notice) if it could not start. */
  /** Keep the foreground service up when a session ends (the wake word is waiting in it). */
  keepService = false;

  /** `wake`: the session was opened by the wake word, so the brain greets and closes quickly. */
  async start(options: { wake?: boolean } = {}): Promise<boolean> {
    if (this.active || this.starting) {
      return false;
    }
    if (this.connection.state !== 'connected') {
      this.set({
        notice:
          'Sem conexão com a Juniper. Confira o Tailscale e tente de novo.',
      });
      return false;
    }
    this.starting = true;
    this.cut.clear();
    this.seq = 0;
    // Keep the person's choice of "hold to talk" across conversations.
    this.set({
      ...initialVoice,
      pushToTalk: this.state.pushToTalk,
      phase: 'starting',
    });
    this.offs = [
      this.connection.onMessage(m => this.onMessage(m)),
      this.connection.onState(s => {
        if (s !== 'connected' && this.active) {
          void this.finish(
            'error',
            'A conexão caiu e a conversa de voz foi encerrada.',
          );
        }
      }),
      this.audio.onChunk(data => this.onChunk(data)),
      this.audio.onStopRequested(() => {
        void this.end();
      }),
      this.audio.onRouteChange(headset => this.changeRoute(headset)),
    ];
    try {
      await this.audio.start(INPUT_RATE);
    } catch {
      await this.finish(
        'error',
        'Não consegui abrir o microfone. Confira a permissão do app.',
      );
      this.starting = false;
      return false;
    }
    const headset = await this.audio.hasHeadset().catch(() => false);
    const route = headset ? 'headset' : 'speaker';
    await this.audio.applyRoute(route === 'speaker').catch(() => undefined);
    this.set({ route });
    const sent = this.connection.send({
      type: 'voice.start',
      id: this.newId(),
      format: { encoding: 'pcm_s16le', sample_rate: INPUT_RATE, channels: 1 },
      local_stt: this.stt.available,
      local_tts: this.tts.available,
      route,
      ...(options.wake ? { wake: true } : {}),
    });
    this.starting = false;
    if (!sent) {
      await this.finish(
        'error',
        'Sem conexão com a Juniper. Confira o Tailscale e tente de novo.',
      );
      return false;
    }
    return true;
  }

  /** The person ends the conversation (button or notification). */
  async end(): Promise<void> {
    if (!this.active) {
      return;
    }
    if (this.sessionOpen) {
      this.connection.send({ type: 'voice.end', reason: 'client' });
    }
    await this.finish('client');
  }

  /** "Shut up" without ending the conversation. */
  interrupt(): void {
    if (this.sessionOpen) {
      this.connection.send({ type: 'voice.interrupt' });
    }
    this.audio.flush();
    this.tts.stop();
  }

  /** Hold-to-talk on or off. With it on, only what is said while holding reaches the brain. */
  setPushToTalk(on: boolean): void {
    this.set({ pushToTalk: on, talking: false });
  }

  /** The talk button went down: cut her off if she is speaking, and open the microphone. */
  pressTalk(): void {
    if (!this.sessionOpen || !this.state.pushToTalk) {
      return;
    }
    if (this.state.phase === 'speaking' || this.state.phase === 'thinking') {
      this.interrupt();
    }
    this.set({ talking: true });
  }

  /** The talk button went up: close the microphone and let the brain hear the utterance end. */
  releaseTalk(): void {
    if (!this.state.talking) {
      return;
    }
    this.set({ talking: false });
    if (this.sessionOpen && this.state.stt === 'cloud') {
      for (let i = 0; i < SILENCE_CHUNKS_AFTER_TALK; i++) {
        this.connection.send({
          type: 'voice.audio',
          seq: this.seq++,
          data: SILENCE_CHUNK,
        });
      }
    }
  }

  private changeRoute(headset: boolean): void {
    if (!this.sessionOpen) {
      return;
    }
    const route = headset ? 'headset' : 'speaker';
    if (route === this.state.route) {
      return;
    }
    this.set({ route });
    this.audio.applyRoute(!headset).catch(() => undefined);
    this.connection.send({ type: 'voice.route', route });
  }

  private onChunk(data: string): void {
    if (!this.sessionOpen || this.state.stt !== 'cloud') {
      return;
    }
    if (this.state.pushToTalk && !this.state.talking) {
      return; // hold-to-talk: the microphone is closed until the button is pressed
    }
    if (this.state.route !== 'headset' && this.state.phase !== 'listening') {
      // Whenever she is not waiting for him (greeting, thinking, answering, commenting) the
      // microphone is closed: on a speaker it would hear her own voice and the music (and the
      // brain ignores it too: both ends keep the same rule).
      return;
    }
    // Nothing is queued while offline: a late chunk is worth nothing.
    this.connection.send({ type: 'voice.audio', seq: this.seq++, data });
  }

  private onMessage(message: AnyMessage & { type: string }): void {
    if (isType(message, 'voice.ready')) {
      void this.ready(message);
    } else if (isType(message, 'voice.state')) {
      this.onState(message);
    } else if (isType(message, 'voice.transcript')) {
      this.set({ userText: message.text });
    } else if (isType(message, 'voice.audio')) {
      if (
        !this.sessionOpen ||
        (message.turn_id && this.cut.has(message.turn_id))
      ) {
        return;
      }
      if (message.data !== '') {
        this.audio.play(message.data);
      }
    } else if (isType(message, 'voice.say')) {
      if (
        this.sessionOpen &&
        !this.cut.has(message.turn_id) &&
        message.text !== ''
      ) {
        this.tts.speak(message.text);
      }
    } else if (isType(message, 'voice.fallback')) {
      this.onFallback(message);
    } else if (isType(message, 'voice.end')) {
      const reason = message.reason ?? 'client';
      void this.finish(reason, END_TEXT[reason] ?? null);
    } else if (isType(message, 'error') && message.code.startsWith('voice_')) {
      void this.finish('error', message.message);
    }
  }

  private async ready(message: Msg<'voice.ready'>): Promise<void> {
    this.sessionOpen = true;
    try {
      await this.audio.openPlayer(message.output.sample_rate);
    } catch {
      await this.finish('error', 'Não consegui abrir o alto-falante.');
      return;
    }
    this.set({ phase: 'listening', stt: message.stt, tts: message.tts });
    if (message.stt === 'local') {
      await this.listenLocally();
    }
  }

  private onState(message: Msg<'voice.state'>): void {
    if (message.state === 'listening' && message.turn_id) {
      // "That turn was cut": drop its audio at once (docs/pages/protocolo.md).
      this.cut.add(message.turn_id);
      this.audio.flush();
      this.tts.stop();
    }
    const phase: VoicePhase | null =
      message.state === 'thinking'
        ? 'thinking'
        : message.state === 'speaking'
        ? 'speaking'
        : 'listening';
    if (this.sessionOpen) {
      this.set({ phase });
    }
  }

  private onFallback(message: Msg<'voice.fallback'>): void {
    if (message.component === 'stt') {
      this.set({ stt: message.mode });
      if (message.mode === 'local') {
        void this.listenLocally();
      } else {
        void this.backToCloud();
      }
    } else {
      this.set({ tts: message.mode });
    }
  }

  private async backToCloud(): Promise<void> {
    await this.stt.stop().catch(() => undefined);
    if (this.sessionOpen) {
      await this.audio.resumeCapture().catch(() => undefined);
    }
  }

  private async listenLocally(): Promise<void> {
    if (!this.stt.available) {
      return;
    }
    try {
      // The recognizer needs the microphone to itself: release the capture while it listens.
      await this.audio.pauseCapture();
      await this.stt.start(
        text => {
          if (this.sessionOpen && text.trim() !== '') {
            this.connection.send({
              type: 'voice.text',
              id: this.newId(),
              text: text.trim(),
            });
          }
        },
        () => undefined,
      );
    } catch {
      // Give the microphone back and carry on: the brain keeps working (text chat still works).
      await this.audio.resumeCapture().catch(() => undefined);
      // The brain keeps working on text; the person can still use the text chat.
      this.set({
        notice: 'Não consegui usar o reconhecimento de voz do aparelho.',
      });
    }
  }

  private async finish(
    reason: string,
    notice: string | null = null,
  ): Promise<void> {
    if (!this.active && !this.starting && this.offs.length === 0) {
      return;
    }
    this.sessionOpen = false;
    this.offs.forEach(off => off());
    this.offs = [];
    this.tts.stop();
    await this.stt.stop().catch(() => undefined);
    this.audio.flush();
    await this.audio.closePlayer().catch(() => undefined);
    await this.audio.stop(this.keepService).catch(() => undefined);
    this.cut.clear();
    this.set({
      phase: 'off',
      notice: notice ?? (reason === 'client' ? null : this.state.notice),
    });
  }
}
