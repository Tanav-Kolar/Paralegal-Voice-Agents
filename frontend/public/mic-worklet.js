// Runs on the audio thread: hands raw microphone frames to the page for resampling.
class MicCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("mic-capture", MicCapture);
