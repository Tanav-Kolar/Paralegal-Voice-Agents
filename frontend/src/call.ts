// Browser side of a demo call. Binary websocket frames are raw 16-bit mono PCM in both
// directions; the text frame "stop" hangs up. The server may send the text frame "clear" to
// drop agent audio that is queued but not yet played (barge-in).
const AGENT_TAIL_SECONDS = 0.4;
const PLAYBACK_BUFFER_SECONDS = 0.2;

export type CallState = "idle" | "connecting" | "live" | "ended" | "error";

type Handlers = {
  onState: (state: CallState, message?: string) => void;
  onLevel: (level: number) => void;
  onAgentLevel: (level: number) => void;
  onAgentSpeaking: (speaking: boolean) => void;
  onTranscript: (speaker: "agent" | "caller", text: string, update?: boolean) => void;
};

export class VoiceCall {
  private ws: WebSocket | null = null;
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private pending: number[] = [];
  private sum = 0;
  private count = 0;
  private position = 0;
  private nextOutput = 0;
  private leftoverByte: number | null = null;
  private playHead = 0;
  private playing = new Set<AudioBufferSourceNode>();
  private speakingTimer: number | null = null;
  private closing = false;
  private agentText = "";
  private agentTextSeconds = 0;
  private agentWordsShown = 0;

  constructor(private readonly handlers: Handlers, private readonly sampleRate: number) {}

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
      this.ws.send("stop");
      this.ws.close();
    }
    this.release();
    this.handlers.onState("ended");
  }

  private openSocket(streamPath: string): Promise<void> {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${scheme}://${location.host}${streamPath}`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    return new Promise((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error("Could not reach the voice service."));
      ws.onmessage = (event) => {
        if (event.data === "clear") this.clearAgentAudio();
        else if (typeof event.data === "string") this.handleEvent(event.data);
        else if (typeof event.data !== "string") this.playAgentAudio(new Uint8Array(event.data));
      };
      ws.onclose = (event) => {
        if (this.closing) return;
        this.closing = true;
        this.release();
        if (event.code === 1013) {
          this.handlers.onState("error", "A call is already running for this account.");
        } else if (event.code === 1008) {
          this.handlers.onState("error", "Your session expired. Reload the page and sign in again.");
        } else {
          this.handlers.onState("ended");
        }
      };
    });
  }

  private handleEvent(payload: string): void {
    try {
      const event: unknown = JSON.parse(payload);
      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "transcript" &&
        "speaker" in event &&
        (event.speaker === "agent" || event.speaker === "caller") &&
        "text" in event &&
        typeof event.text === "string"
      ) {
        if (event.speaker === "agent") {
          this.agentText = event.text;
          this.agentTextSeconds = 0;
          this.agentWordsShown = 0;
          this.handlers.onTranscript("agent", "");
        } else {
          this.handlers.onTranscript("caller", event.text);
        }
      } else if (
        typeof event === "object" && event !== null && "type" in event &&
        event.type === "agent_transcript_done"
      ) {
        this.handlers.onTranscript("agent", this.agentText, true);
        this.agentText = "";
      }
    } catch {
      // Ignore unrelated textual control frames.
    }
  }

  private captureMicrophone(context: AudioContext, stream: MediaStream): void {
    const ratio = context.sampleRate / this.sampleRate;
    const chunkSamples = this.sampleRate / 10; // 100 ms
    this.nextOutput = ratio;
    const node = new AudioWorkletNode(context, "mic-capture");
    node.port.onmessage = (event: MessageEvent<Float32Array>) => {
      let peak = 0;
      for (const sample of event.data) {
        peak = Math.max(peak, Math.abs(sample));
        this.sum += sample;
        this.count += 1;
        this.position += 1;
        if (this.position >= this.nextOutput) {
          // The server gates demo microphone audio while the agent is speaking.
          this.pending.push(this.sum / this.count);
          this.sum = 0;
          this.count = 0;
          this.nextOutput += ratio;
        }
      }
      this.handlers.onLevel(peak);
      while (this.pending.length >= chunkSamples) this.sendChunk(this.pending.splice(0, chunkSamples));
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
    this.ws.send(pcm.buffer);
  }

  private playAgentAudio(incoming: Uint8Array): void {
    const context = this.context;
    if (!context) return;
    const bytes = new Uint8Array(incoming.length + (this.leftoverByte === null ? 0 : 1));
    let offset = 0;
    if (this.leftoverByte !== null) bytes[offset++] = this.leftoverByte;
    bytes.set(incoming, offset);
    const usable = bytes.length - (bytes.length % 2);
    this.leftoverByte = usable < bytes.length ? bytes[usable] : null;
    if (usable === 0) return;
    const pcm = new Int16Array(bytes.buffer.slice(0, usable));
    const buffer = context.createBuffer(1, pcm.length, this.sampleRate);
    this.advanceAgentText(buffer.duration);
    const channel = buffer.getChannelData(0);
    let peak = 0;
    for (let i = 0; i < pcm.length; i++) {
      channel[i] = pcm[i] / 0x8000;
      peak = Math.max(peak, Math.abs(channel[i]));
    }
    this.handlers.onAgentLevel(peak);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    // Give incoming websocket frames enough lead time to absorb normal network
    // and main-thread jitter without inserting silence between audio buffers.
    this.playHead = Math.max(this.playHead, context.currentTime + PLAYBACK_BUFFER_SECONDS);
    source.onended = () => this.playing.delete(source);
    this.playing.add(source);
    source.start(this.playHead);
    this.playHead += buffer.duration;
    this.handlers.onAgentSpeaking(true);
    if (this.speakingTimer) window.clearTimeout(this.speakingTimer);
    this.speakingTimer = window.setTimeout(
      () => this.handlers.onAgentSpeaking(this.agentIsSpeaking(context)),
      Math.max(0, (this.playHead - context.currentTime + AGENT_TAIL_SECONDS) * 1000),
    );
  }

  private clearAgentAudio(): void {
    this.playing.forEach((source) => source.stop());
    this.playing.clear();
    if (this.speakingTimer) window.clearTimeout(this.speakingTimer);
    this.speakingTimer = null;
    this.leftoverByte = null;
    this.playHead = 0;
    this.agentText = "";
    this.handlers.onAgentSpeaking(false);
    this.handlers.onAgentLevel(0);
  }

  private advanceAgentText(seconds: number): void {
    if (!this.agentText) return;
    this.agentTextSeconds += seconds;
    const words = this.agentText.trim().split(/\s+/);
    const shown = Math.min(words.length, Math.max(1, Math.floor(this.agentTextSeconds * 3)));
    if (shown > this.agentWordsShown) {
      this.agentWordsShown = shown;
      this.handlers.onTranscript("agent", words.slice(0, shown).join(" "), true);
    }
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
    this.handlers.onAgentLevel(0);
  }
}
