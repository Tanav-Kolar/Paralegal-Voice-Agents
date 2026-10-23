import { useRef, useState } from "react";

import { startDemoSession } from "./api";
import { type CallState, VoiceCall } from "./call";

export function CallPanel({ onFinished }: { onFinished: () => void }) {
  const [state, setState] = useState<CallState>("idle");
  const [message, setMessage] = useState("");
  const [level, setLevel] = useState(0);
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const call = useRef<VoiceCall | null>(null);

  async function begin() {
    setMessage("");
    setState("connecting");
    try {
      const session = await startDemoSession();
      call.current = new VoiceCall({
        onState: (next, detail) => {
          setState(next);
          setMessage(detail ?? "");
        },
        onLevel: setLevel,
        onAgentSpeaking: setAgentSpeaking,
      });
      await call.current.start(session.streamPath);
    } catch (reason) {
      setState("error");
      setMessage(reason instanceof Error ? reason.message : "The call could not start.");
    }
  }

  const busy = state === "connecting" || state === "live";
  const status =
    state === "connecting"
      ? "Connecting…"
      : state === "live"
        ? agentSpeaking
          ? "The agent is speaking"
          : "Listening to you"
        : state === "ended"
          ? "Call ended"
          : "Ready";

  return (
    <section className="call-panel" aria-label="Try the intake agent">
      <p className="call-lede">
        Talk to the agent as a caller would. It asks which language you prefer, asks for consent
        to record, then takes the intake. Use made-up details and headphones.
      </p>
      <div className="call-controls">
        {busy ? (
          <button className="play" onClick={() => call.current?.stop()} type="button">
            End call
          </button>
        ) : (
          <button className="play" onClick={() => void begin()} type="button">
            Start call
          </button>
        )}
        <p className="call-status" role="status">
          {status}
        </p>
        <div className="meter" aria-hidden="true">
          <span style={{ width: `${Math.min(100, level * 160)}%` }} />
        </div>
      </div>
      {message && <p className="message error">{message}</p>}
      {state === "ended" && (
        <p className="call-done">
          The call is saved.{" "}
          <button className="refresh" onClick={onFinished} type="button">
            Open reviewed calls
          </button>
        </p>
      )}
    </section>
  );
}
