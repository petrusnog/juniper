import { describe, expect, test } from 'vitest';
import { RemoteAudio } from '../src/core/voice/RemoteAudio';
import type { AudioCommand, AudioEvent } from '../src/shared/audioProtocol';
import { base64ToBytes, bytesToBase64, decodeChunk, encodeChunk, floatToPcm16, pcm16ToFloat, resample } from '../src/shared/pcm';

describe('pcm', () => {
  test('float to PCM16 clips and keeps the sign', () => {
    expect(Array.from(floatToPcm16(new Float32Array([0, 1, -1, 2, -2])))).toEqual([0, 32767, -32768, 32767, -32768]);
  });

  test('round trip through the protocol chunk is faithful at the same rate', () => {
    const input = new Float32Array([0, 0.25, -0.25, 0.5, -0.5]);
    const back = decodeChunk(encodeChunk(input, 16000, 16000));
    back.forEach((v, i) => expect(v).toBeCloseTo(input[i] as number, 3));
  });

  test('48 kHz becomes 16 kHz (one sample in three), 100 ms is 3200 bytes', () => {
    const out = resample(new Float32Array(4800).fill(0.5), 48000, 16000);
    expect(out).toHaveLength(1600);
    expect(base64ToBytes(encodeChunk(new Float32Array(4800), 48000, 16000))).toHaveLength(3200);
  });

  test('a half sample at the end of a chunk is dropped, not turned into noise', () => {
    const odd = bytesToBase64(new Uint8Array([1, 0, 2, 0, 3]));
    expect(decodeChunk(odd)).toHaveLength(2);
  });

  test('silence stays silence', () => {
    expect(Array.from(pcm16ToFloat(new Int16Array(4)))).toEqual([0, 0, 0, 0]);
  });
});

class Window {
  commands: AudioCommand[] = [];
  private listener: ((e: AudioEvent) => void) | null = null;
  readonly transport = {
    send: (c: AudioCommand) => void this.commands.push(c),
    onEvent: (l: (e: AudioEvent) => void) => {
      this.listener = l;
      return () => (this.listener = null);
    },
  };
  emit(e: AudioEvent) {
    this.listener?.(e);
  }
}

describe('RemoteAudio', () => {
  test('start waits for the window to open the microphone', async () => {
    const w = new Window();
    const audio = new RemoteAudio(w.transport);
    const started = audio.start(16000);
    expect(w.commands[0]).toMatchObject({ kind: 'capture-start', rate: 16000 });
    w.emit({ kind: 'ack', id: (w.commands[0] as { id: number }).id, ok: true });
    await expect(started).resolves.toBeUndefined();
  });

  test('a refused microphone rejects start (the controller then ends with a notice)', async () => {
    const w = new Window();
    const audio = new RemoteAudio(w.transport);
    const started = audio.start(16000);
    w.emit({ kind: 'ack', id: (w.commands[0] as { id: number }).id, ok: false, error: 'NotAllowedError' });
    await expect(started).rejects.toThrow('NotAllowedError');
  });

  test('a window that never answers times out', async () => {
    const w = new Window();
    let fire: () => void = () => undefined;
    const audio = new RemoteAudio(w.transport, fn => ((fire = fn), 1), () => undefined);
    const started = audio.openPlayer(24000);
    fire();
    await expect(started).rejects.toThrow('timeout');
  });

  test('chunks, stop request and headphones come from the window', async () => {
    const w = new Window();
    const audio = new RemoteAudio(w.transport);
    const got: string[] = [];
    let stops = 0;
    const routes: boolean[] = [];
    audio.onChunk(d => got.push(d));
    audio.onStopRequested(() => (stops += 1));
    audio.onRouteChange(h => routes.push(h));
    w.emit({ kind: 'chunk', data: 'AAAA' });
    w.emit({ kind: 'stop-requested' });
    w.emit({ kind: 'headset', value: true });
    expect(got).toEqual(['AAAA']);
    expect(stops).toBe(1);
    expect(routes).toEqual([true]);
    expect(await audio.hasHeadset()).toBe(true);
  });

  test('play and flush are sent as they are', () => {
    const w = new Window();
    const audio = new RemoteAudio(w.transport);
    audio.play('QUJD');
    audio.flush();
    expect(w.commands).toEqual([{ kind: 'play', data: 'QUJD' }, { kind: 'flush' }]);
  });

  test('dispose rejects what is still waiting', async () => {
    const w = new Window();
    const audio = new RemoteAudio(w.transport);
    const started = audio.start(16000);
    audio.dispose();
    await expect(started).rejects.toThrow('disposed');
  });
});

import { parseAudioEvent } from '../src/shared/audioProtocol';

describe('parseAudioEvent (what the main process accepts from the window)', () => {
  test.each([
    [{ kind: 'ack', id: 3, ok: true }, true],
    [{ kind: 'chunk', data: 'AAAA' }, true],
    [{ kind: 'stop-requested' }, true],
    [{ kind: 'headset', value: false }, true],
    [null, false],
    ['chunk', false],
    [{ kind: 'unknown' }, false],
    [{ kind: 'ack', id: '3', ok: true }, false],
    [{ kind: 'ack', id: 1.5, ok: true }, false],
    [{ kind: 'chunk', data: 5 }, false],
    [{ kind: 'chunk', data: 'A'.repeat(70_000) }, false],
    [{ kind: 'headset', value: 'yes' }, false],
  ])('%j -> %s', (event, accepted) => {
    expect(parseAudioEvent(event) !== null).toBe(accepted);
  });

  test('an error text from the window is cut short', () => {
    const e = parseAudioEvent({ kind: 'ack', id: 1, ok: false, error: 'x'.repeat(500) });
    expect(e && 'error' in e && e.error?.length).toBe(80);
  });
});
