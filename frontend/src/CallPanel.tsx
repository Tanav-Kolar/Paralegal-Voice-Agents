import { useEffect, useRef, useState, type CSSProperties } from "react";

import { startDemoSession } from "./api";
import { type CallState, VoiceCall } from "./call";

type TranscriptItem = { id: number; speaker: "agent" | "caller"; text: string };

function Waveform({ active, tone }: { active: boolean; tone: "agent" | "caller" }) {
  return <span className={`voice-wave ${tone} ${active ? "active" : ""}`} aria-hidden="true">{Array.from({ length: 14 }, (_, index) => <i key={index} style={{ "--bar": index, "--height": `${10 + (index % 6) * 4}px` } as CSSProperties} />)}</span>;
}

export function CallPanel({ onFinished }: { onFinished: () => void }) {
  const [state, setState] = useState<CallState>("idle");
  const [message, setMessage] = useState("");
  const [callerLevel, setCallerLevel] = useState(0);
  const [agentLevel, setAgentLevel] = useState(0);
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const [transcript, setTranscript] = useState<TranscriptItem[]>([]);
  const transcriptEnd = useRef<HTMLDivElement | null>(null);
  const callerTimer = useRef<number | null>(null);
  const call = useRef<VoiceCall | null>(null);

  useEffect(() => { transcriptEnd.current?.scrollIntoView({ block: "end", behavior: "smooth" }); }, [transcript]);
  useEffect(() => () => { if (callerTimer.current) window.clearTimeout(callerTimer.current); }, []);

  function updateCallerLevel(level: number) {
    setCallerLevel(level);
    if (level > 0.018) {
      if (callerTimer.current) window.clearTimeout(callerTimer.current);
      callerTimer.current = window.setTimeout(() => setCallerLevel(0), 180);
    }
  }

  async function begin() {
    setMessage(""); setTranscript([]); setState("connecting");
    try {
      const session = await startDemoSession();
      call.current = new VoiceCall({
        onState: (next, detail) => { setState(next); setMessage(detail ?? ""); },
        onLevel: updateCallerLevel, onAgentLevel: setAgentLevel, onAgentSpeaking: setAgentSpeaking,
        onTranscript: (speaker, text, update) => setTranscript((items) => {
          if (update && items.at(-1)?.speaker === "agent") {
            return [...items.slice(0, -1), { ...items[items.length - 1], text }];
          }
          return [...items, { id: Date.now() + items.length, speaker, text }];
        }),
      });
      await call.current.start(session.streamPath);
    } catch (reason) { setState("error"); setMessage(reason instanceof Error ? reason.message : "The call could not start."); }
  }

  const busy = state === "connecting" || state === "live";
  const callerSpeaking = callerLevel > 0.018 && !agentSpeaking;
  const status = state === "connecting" ? "Connecting…" : state === "live" ? agentSpeaking ? "Agent speaking — please wait" : callerSpeaking ? "Listening to you" : "Listening" : state === "ended" ? "Call ended" : "Ready";

  return <section className="call-panel" aria-label="Try the intake agent">
    <header className="call-intro"><div><p className="call-kicker">Live conversation</p><h1>Try the intake agent</h1></div><p>Speak naturally in English. Give consent, then wait until the agent finishes before replying.</p></header>
    <div className="call-room">
      <section className="call-stage" aria-label="Call controls">
        <div className={`speaker-orb agent-orb ${agentSpeaking ? "speaking" : ""}`}><span className="orb-label">Agent</span><Waveform active={agentSpeaking && agentLevel > 0} tone="agent" /></div>
        <div className="call-status-block"><strong>{status}</strong><span>{busy ? "Audio is live" : "Start when you are ready"}</span></div>
        <div className={`speaker-orb caller-orb ${callerSpeaking ? "speaking" : ""}`}><span className="orb-label">You</span><Waveform active={callerSpeaking} tone="caller" /></div>
        {busy ? <button className="play end-call" onClick={() => call.current?.stop()} type="button">End call</button> : <button className="play" onClick={() => void begin()} type="button">Start call</button>}
        <p className="call-hint">Headphones help keep playback out of your microphone.</p>
      </section>
      <section className="live-transcript" aria-label="Live transcript" aria-live="polite">
        <header><div><p className="call-kicker">Live transcript</p><h2>Conversation</h2></div><span className={`live-dot ${busy ? "active" : ""}`}>{busy ? "Live" : "Waiting"}</span></header>
        <div className="transcript-list">{transcript.length === 0 ? <p className="transcript-empty">The conversation will appear here as each person speaks.</p> : transcript.map((item) => <article className={`transcript-turn ${item.speaker}`} key={item.id}><span className="speaker-tag">{item.speaker === "agent" ? "Agent" : "Caller"}</span><p>{item.text || "…"}</p></article>)}<div ref={transcriptEnd} /></div>
      </section>
    </div>
    {message && <p className="message error">{message}</p>}
    {state === "ended" && <p className="call-done">The call is saved. <button className="refresh" onClick={onFinished} type="button">Open reviewed calls</button></p>}
  </section>;
}
