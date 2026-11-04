/** AudioWorklet: hands the microphone's float blocks to the window (it resamples and encodes). */
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}
declare function registerProcessor(name: string, processor: new () => AudioWorkletProcessor): void;

class PcmCapture extends AudioWorkletProcessor {
  process(inputs: Float32Array[][]): boolean {
    const channel = inputs[0]?.[0];
    if (channel !== undefined && channel.length > 0) {
      this.port.postMessage(channel.slice(0));
    }
    return true;
  }
}

registerProcessor('pcm-capture', PcmCapture);
