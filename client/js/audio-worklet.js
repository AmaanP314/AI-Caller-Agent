// audio-worklet.js

// --- Simple Resampler ---
// This uses linear interpolation. It's not perfect, but it's fast and good enough.
class Resampler {
  constructor(fromSampleRate, toSampleRate, numChannels) {
    this.fromSampleRate = fromSampleRate;
    this.toSampleRate = toSampleRate;
    this.numChannels = numChannels;
    this.ratio = fromSampleRate / toSampleRate;
    this.lastSample = new Float32Array(numChannels);
  }

  resample(buffer) {
    const outLength = Math.floor(buffer.length / this.ratio);
    const outBuffer = new Float32Array(outLength);

    for (let i = 0; i < outLength; i++) {
      const inIndex = i * this.ratio;
      const inIndexFloor = Math.floor(inIndex);
      const inIndexCeil = Math.min(buffer.length - 1, inIndexFloor + 1);
      const frac = inIndex - inIndexFloor;

      // Linear interpolation
      outBuffer[i] =
        buffer[inIndexFloor] +
        (buffer[inIndexCeil] - buffer[inIndexFloor]) * frac;
    }
    return outBuffer;
  }
}

// --- Audio Processor Worklet ---
class AudioProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.targetSampleRate = 16000; // Hard-code to 16kHz for the server
    this.resampler = null;
    this.chunkSize = 1024; // Send 1024 samples (Int16) at a time
    this.pcmBuffer = new Int16Array(this.chunkSize);
    this.pcmBufferIndex = 0;
  }

  // --- Float32 to Int16 Conversion ---
  float32ToInt16(buffer) {
    let len = buffer.length;
    let out = new Int16Array(len);
    for (let i = 0; i < len; i++) {
      let s = Math.max(-1, Math.min(1, buffer[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }

  process(inputs, outputs, parameters) {
    // We only care about the first input's first channel
    const input = inputs[0];
    if (input.length === 0) {
      return true;
    }
    const inputData = input[0]; // Float32Array

    // Initialize resampler on first run
    if (!this.resampler) {
      // 'currentFrame' is a global property in AudioWorkletProcessor
      // 'sampleRate' is the sample rate of the AudioContext
      if (sampleRate === this.targetSampleRate) {
        // No resampling needed
        this.resampler = null;
      } else {
        this.resampler = new Resampler(sampleRate, this.targetSampleRate, 1);
      }
    }

    let pcmData;
    if (this.resampler) {
      // 1. Resample from (e.g.) 48kHz to 16kHz
      const resampledData = this.resampler.resample(inputData);
      // 2. Convert to 16-bit PCM
      pcmData = this.float32ToInt16(resampledData);
    } else {
      // Already at target rate, just convert
      pcmData = this.float32ToInt16(inputData);
    }

    // 3. Buffer and send chunks
    for (let i = 0; i < pcmData.length; i++) {
      this.pcmBuffer[this.pcmBufferIndex++] = pcmData[i];

      if (this.pcmBufferIndex === this.chunkSize) {
        // Chunk is full, send it
        // We post the buffer (ArrayBuffer) to transfer ownership
        this.port.postMessage(this.pcmBuffer, [this.pcmBuffer.buffer]);

        // Create a new buffer for the next chunk
        this.pcmBuffer = new Int16Array(this.chunkSize);
        this.pcmBufferIndex = 0;
      }
    }

    return true; // Keep the processor alive
  }
}

registerProcessor("audio-processor", AudioProcessor);
