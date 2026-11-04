/**
 * The window's audio engine (ADR-033, ADR-023): the microphone with the browser's echo
 * cancellation and noise suppression, resampled to 16 kHz PCM in 100 ms chunks, and the playback
 * of her voice. It only talks to the main process through the bridge; nothing is stored.
 */
import type { AudioCommand } from '../shared/audioProtocol';
import type { JuniperBridge } from '../shared/ipc';
import { decodeChunk, encodeChunk } from '../shared/pcm';

const CHUNK_MS = 100;
const MAX_QUEUED_SECONDS = 60;

export function startAudioEngine(api: JuniperBridge): void {
  let capture: {
    context: AudioContext;
    stream: MediaStream;
    node: AudioWorkletNode;
    paused: boolean;
  } | null = null;
  let player: { context: AudioContext; rate: number; next: number; sources: Set<AudioBufferSourceNode> } | null =
    null;

  const ack = (id: number, ok: boolean, error?: string) =>
    void api.audioEvent({ kind: 'ack', id, ok, ...(error ? { error } : {}) });

  async function startCapture(id: number, rate: number): Promise<void> {
    try {
      if (capture !== null) await closeCapture(); // never leave an earlier microphone open
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
      const context = new AudioContext();
      await context.audioWorklet.addModule('pcm-worklet.js');
      const node = new AudioWorkletNode(context, 'pcm-capture');
      const buffer: Float32Array[] = [];
      let buffered = 0;
      const wanted = Math.round((context.sampleRate * CHUNK_MS) / 1000);
      node.port.onmessage = (e: MessageEvent<Float32Array>) => {
        if (capture?.paused) return;
        buffer.push(e.data);
        buffered += e.data.length;
        if (buffered < wanted) return;
        const all = new Float32Array(buffered);
        let at = 0;
        for (const part of buffer) {
          all.set(part, at);
          at += part.length;
        }
        buffer.length = 0;
        buffered = 0;
        void api.audioEvent({ kind: 'chunk', data: encodeChunk(all, context.sampleRate, rate) });
      };
      context.createMediaStreamSource(stream).connect(node);
      capture = { context, stream, node, paused: false };
      ack(id, true);
    } catch (e) {
      ack(id, false, e instanceof Error ? e.name : 'audio');
    }
  }

  async function closeCapture(): Promise<void> {
    const current = capture;
    capture = null;
    current?.stream.getTracks().forEach(t => t.stop());
    await current?.context.close().catch(() => undefined);
  }

  async function stopCapture(id: number): Promise<void> {
    await closeCapture();
    ack(id, true);
  }

  async function openPlayer(id: number, rate: number): Promise<void> {
    try {
      if (!Number.isFinite(rate) || rate < 8000 || rate > 48000) throw new Error('bad rate');
      closePlayer();
      player = { context: new AudioContext(), rate, next: 0, sources: new Set() };
      ack(id, true);
    } catch (e) {
      ack(id, false, e instanceof Error ? e.name : 'audio');
    }
  }

  function closePlayer(): void {
    flush();
    void player?.context.close().catch(() => undefined);
    player = null;
  }

  function play(data: string): void {
    if (player === null) return;
    // Never queue more than a minute ahead: a flood from the other end would fill the memory.
    if (player.next - player.context.currentTime > MAX_QUEUED_SECONDS) return;
    let samples: Float32Array;
    try {
      samples = decodeChunk(data);
    } catch {
      return; // invalid base64: this chunk is lost, the conversation goes on
    }
    if (samples.length === 0) return;
    const { context, rate } = player;
    const buffer = context.createBuffer(1, samples.length, rate);
    buffer.copyToChannel(new Float32Array(samples), 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const start = Math.max(context.currentTime + 0.02, player.next);
    source.start(start);
    player.next = start + buffer.duration;
    player.sources.add(source);
    source.onended = () => player?.sources.delete(source);
  }

  function flush(): void {
    if (player === null) return;
    player.sources.forEach(s => {
      try {
        s.stop();
      } catch {
        /* already finished */
      }
    });
    player.sources.clear();
    player.next = 0;
  }

  api.onAudioCommand((c: AudioCommand) => {
    switch (c.kind) {
      case 'capture-start':
        void startCapture(c.id, c.rate);
        break;
      case 'capture-stop':
        void stopCapture(c.id);
        break;
      case 'capture-pause':
        if (capture) capture.paused = true;
        break;
      case 'capture-resume':
        if (capture) capture.paused = false;
        break;
      case 'player-open':
        void openPlayer(c.id, c.rate);
        break;
      case 'play':
        play(c.data);
        break;
      case 'flush':
        flush();
        break;
      case 'player-close':
        closePlayer();
        ack(c.id, true);
        break;
    }
  });
}
