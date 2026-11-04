/**
 * PCM helpers shared by the window (capture, playback) and the tests: float samples to 16-bit
 * little-endian PCM at the rate the brain wants (16 kHz), and back. Pure functions.
 */

/** Averaging resample (a box filter) from `inRate` to `outRate`, enough for speech at 16 kHz. */
export function resample(input: Float32Array, inRate: number, outRate: number): Float32Array {
  if (inRate === outRate) return input;
  const ratio = inRate / outRate;
  const length = Math.floor(input.length / ratio);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += input[j] ?? 0;
    out[i] = end > start ? sum / (end - start) : 0;
  }
  return out;
}

export function floatToPcm16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i] ?? 0));
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
  }
  return out;
}

export function pcm16ToFloat(pcm: Int16Array): Float32Array {
  const out = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i += 1) {
    const v = pcm[i] ?? 0;
    out[i] = v < 0 ? v / 0x8000 : v / 0x7fff;
  }
  return out;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

export function base64ToBytes(text: string): Uint8Array {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
  return out;
}

/** Microphone samples at `inRate` as the base64 PCM16 chunk of the protocol (`voice.audio`). */
export function encodeChunk(samples: Float32Array, inRate: number, outRate: number): string {
  const pcm = floatToPcm16(resample(samples, inRate, outRate));
  return bytesToBase64(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength));
}

/** The protocol's base64 PCM16 chunk as float samples for the speakers. */
export function decodeChunk(base64: string): Float32Array {
  const bytes = base64ToBytes(base64);
  const aligned = bytes.byteLength - (bytes.byteLength % 2); // a half sample is dropped
  return pcm16ToFloat(new Int16Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + aligned)));
}
