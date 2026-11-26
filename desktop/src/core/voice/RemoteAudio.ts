/**
 * `AudioDevice` for a window that owns the microphone and the speakers (ADR-033). The main
 * process holds the voice session; the window captures and plays. Commands that can fail
 * (opening the microphone or the speakers) wait for the window's acknowledgement.
 */
import type { AudioCommand, AudioEvent } from '../../shared/audioProtocol';
import type { AudioDevice } from './devices';

export interface AudioTransport {
  send(command: AudioCommand): void;
  /** Registers the listener for the window's events; returns the unsubscribe function. */
  onEvent(listener: (event: AudioEvent) => void): () => void;
}

const ACK_TIMEOUT_MS = 5000;

export class RemoteAudio implements AudioDevice {
  private nextId = 1;
  private readonly waiting = new Map<number, { ok: () => void; fail: (e: Error) => void }>();
  private readonly chunkListeners = new Set<(data: string) => void>();
  private readonly stopListeners = new Set<() => void>();
  private readonly routeListeners = new Set<(headset: boolean) => void>();
  private readonly playbackListeners = new Set<(playing: boolean) => void>();
  private headset = false;
  private readonly off: () => void;

  constructor(
    private readonly transport: AudioTransport,
    private readonly setTimer: (fn: () => void, ms: number) => unknown = (fn, ms) =>
      setTimeout(fn, ms),
    private readonly clearTimer: (h: unknown) => void = h =>
      clearTimeout(h as ReturnType<typeof setTimeout>),
  ) {
    this.off = transport.onEvent(event => this.handle(event));
  }

  dispose(): void {
    this.off();
    this.waiting.forEach(w => w.fail(new Error('disposed')));
    this.waiting.clear();
  }

  private handle(event: AudioEvent): void {
    if (event.kind === 'ack') {
      const pending = this.waiting.get(event.id);
      this.waiting.delete(event.id);
      if (pending) {
        if (event.ok) pending.ok();
        else pending.fail(new Error(event.error ?? 'audio'));
      }
    } else if (event.kind === 'chunk') {
      this.chunkListeners.forEach(l => l(event.data));
    } else if (event.kind === 'stop-requested') {
      this.stopListeners.forEach(l => l());
    } else if (event.kind === 'playback') {
      this.playbackListeners.forEach(l => l(event.playing));
    } else if (event.kind === 'headset') {
      this.headset = event.value;
      this.routeListeners.forEach(l => l(event.value));
    }
  }

  private request(build: (id: number) => AudioCommand): Promise<void> {
    const id = this.nextId++;
    return new Promise<void>((resolve, reject) => {
      const timer = this.setTimer(() => {
        this.waiting.delete(id);
        reject(new Error('timeout'));
      }, ACK_TIMEOUT_MS);
      this.waiting.set(id, {
        ok: () => {
          this.clearTimer(timer);
          resolve();
        },
        fail: e => {
          this.clearTimer(timer);
          reject(e);
        },
      });
      this.transport.send(build(id));
    });
  }

  start(sampleRate: number): Promise<void> {
    return this.request(id => ({ kind: 'capture-start', id, rate: sampleRate }));
  }
  stop(): Promise<void> {
    return this.request(id => ({ kind: 'capture-stop', id }));
  }
  onChunk(listener: (base64Pcm: string) => void): () => void {
    this.chunkListeners.add(listener);
    return () => this.chunkListeners.delete(listener);
  }
  onStopRequested(listener: () => void): () => void {
    this.stopListeners.add(listener);
    return () => this.stopListeners.delete(listener);
  }
  async pauseCapture(): Promise<void> {
    this.transport.send({ kind: 'capture-pause' });
  }
  async resumeCapture(): Promise<void> {
    this.transport.send({ kind: 'capture-resume' });
  }
  async hasHeadset(): Promise<boolean> {
    return this.headset;
  }
  async applyRoute(): Promise<void> {
    // Echo cancellation is always on in the window; the route only changes what the brain does.
  }
  onRouteChange(listener: (headset: boolean) => void): () => void {
    this.routeListeners.add(listener);
    return () => this.routeListeners.delete(listener);
  }
  onPlayback(listener: (playing: boolean) => void): () => void {
    this.playbackListeners.add(listener);
    return () => this.playbackListeners.delete(listener);
  }
  openPlayer(sampleRate: number): Promise<void> {
    return this.request(id => ({ kind: 'player-open', id, rate: sampleRate }));
  }
  play(base64Pcm: string): void {
    this.transport.send({ kind: 'play', data: base64Pcm });
  }
  flush(): void {
    this.transport.send({ kind: 'flush' });
  }
  closePlayer(): Promise<void> {
    return this.request(id => ({ kind: 'player-close', id }));
  }
}
