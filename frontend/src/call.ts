// Browser side of a demo call. Speaks the same JSON protocol as an Exotel Voicebot stream:
// 16-bit mono PCM at 8 kHz, base64-encoded, in start / media / stop events.
const SAMPLE_RATE = 8000;
const CHUNK_SAMPLES = 800; // 100 ms
const AGENT_TAIL_SECONDS = 0.4;

export type CallState = "idle" | "connecting" | "live" | "ended" | "error";

type Handlers = {
  onState: (state: CallState, message?: string) => void;
  onLevel: (level: number) => void;
  onAgentSpeaking: (speaking: boolean) => void;
};

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

export class VoiceCall {
  private ws: WebSocket | null = null;
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private streamSid = crypto.randomUUID();
  private pending: number[] = [];
  private sum = 0;
  private count = 0;
  private position = 0;
  private nextOutput = 0;
  private leftoverByte: number | null = null;
  private playHead = 0;
  private speakingTimer: number | null = null;
  private closing = false;

  constructor(private readonly handlers: Handlers) {}

  async start(streamPath: string): Promise<void> {
    this.handlers.onState("connecting");
    try {
      // Ask for the microphone first so a permission prompt cannot stall the socket.
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      this.context = new AudioContext();
      await this.context.audioWorklet.addModule("/mic-worklet.js");
      await this.openSocket(streamPath);
      this.captureMicrophone(this.context, this.stream);
      this.handlers.onState("live");
    } catch (error) {
      this.closing = true;
      this.ws?.close();
      this.release();
      this.handlers.onState(
        "error",
        error instanceof DOMException && error.name === "NotAllowedError"
          ? "Microphone access was blocked. Allow it in the browser and try again."
          : error instanceof Error
            ? error.message
            : "The call could not start.",
      );
    }
  }

  stop(): void {
    if (this.closing) return;
    this.closing = true;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ event: "stop" }));
      this.ws.close();
    }
    this.release();
    this.handlers.onState("ended");
  }

  private openSocket(streamPath: string): Promise<void> {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${scheme}://${location.host}${streamPath}`);
    this.ws = ws;
    return new Promise((resolve, reject) => {
      ws.onopen = () => {
        ws.send(JSON.stringify({ event: "start", start: { stream_sid: this.streamSid } }));
        resolve();
      };
      ws.onerror = () => reject(new Error("Could not reach the voice service."));
      ws.onmessage = (event) => this.playAgentAudio(event.data as string);
      ws.onclose = (event) => {
        if (this.closing) return;
        this.closing = true;
        this.release();
        if (event.code === 1013) {
          this.handlers.onState("error", "A call is already running for this access code.");
        } else if (event.code === 1008) {
          this.handlers.onState("error", "Your access expired. Reload the page and sign in again.");
        } else {
          this.handlers.onState("ended");
        }
      };
    });
  }

  private captureMicrophone(context: AudioContext, stream: MediaStream): void {
    const ratio = context.sampleRate / SAMPLE_RATE;
    this.nextOutput = ratio;
    const node = new AudioWorkletNode(context, "mic-capture");
    node.port.onmessage = (event: MessageEvent<Float32Array>) => {
      const muted = this.agentIsSpeaking(context);
      let peak = 0;
      for (const sample of event.data) {
        peak = Math.max(peak, Math.abs(sample));
        this.sum += sample;
        this.count += 1;
        this.position += 1;
        if (this.position >= this.nextOutput) {
          // Box-filter downsample to 8 kHz. While the agent talks, send silence so the
          // microphone cannot pick up the agent's own voice (no barge-in in the demo).
          this.pending.push(muted ? 0 : this.sum / this.count);
          this.sum = 0;
          this.count = 0;
          this.nextOutput += ratio;
        }
      }
      this.handlers.onLevel(muted ? 0 : peak);
      while (this.pending.length >= CHUNK_SAMPLES) this.sendChunk(this.pending.splice(0, CHUNK_SAMPLES));
    };
    const silent = context.createGain();
    silent.gain.value = 0;
    context.createMediaStreamSource(stream).connect(node).connect(silent).connect(context.destination);
  }

  private sendChunk(samples: number[]): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const pcm = new Int16Array(samples.length);
    samples.forEach((sample, i) => {
      pcm[i] = Math.max(-1, Math.min(1, sample)) * 0x7fff;
    });
    this.ws.send(
      JSON.stringify({
        event: "media",
        stream_sid: this.streamSid,
        media: { payload: toBase64(new Uint8Array(pcm.buffer)) },
      }),
    );
  }

  private playAgentAudio(raw: string): void {
    const context = this.context;
    if (!context) return;
    const message = JSON.parse(raw);
    if (message.event !== "media") return;
    const decoded = atob(message.media.payload);
    const bytes = new Uint8Array(decoded.length + (this.leftoverByte === null ? 0 : 1));
    let offset = 0;
    if (this.leftoverByte !== null) bytes[offset++] = this.leftoverByte;
    for (let i = 0; i < decoded.length; i++) bytes[offset + i] = decoded.charCodeAt(i);
    const usable = bytes.length - (bytes.length % 2);
    this.leftoverByte = usable < bytes.length ? bytes[usable] : null;
    if (usable === 0) return;
    const pcm = new Int16Array(bytes.buffer.slice(0, usable));
    const buffer = context.createBuffer(1, pcm.length, SAMPLE_RATE);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 0x8000;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    this.playHead = Math.max(this.playHead, context.currentTime + 0.05);
    source.start(this.playHead);
    this.playHead += buffer.duration;
    this.handlers.onAgentSpeaking(true);
    if (this.speakingTimer) window.clearTimeout(this.speakingTimer);
    this.speakingTimer = window.setTimeout(
      () => this.handlers.onAgentSpeaking(this.agentIsSpeaking(context)),
      Math.max(0, (this.playHead - context.currentTime + AGENT_TAIL_SECONDS) * 1000),
    );
  }

  private agentIsSpeaking(context: AudioContext): boolean {
    return this.playHead + AGENT_TAIL_SECONDS > context.currentTime;
  }

  private release(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    void this.context?.close();
    if (this.speakingTimer) window.clearTimeout(this.speakingTimer);
    this.stream = null;
    this.context = null;
    this.handlers.onAgentSpeaking(false);
    this.handlers.onLevel(0);
  }
}
