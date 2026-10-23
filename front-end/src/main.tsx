import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import {
  type IntakeCall,
  loadIntakeCalls,
  recordingPlaybackUrl,
} from "./api";
import "./styles.css";

function formatTimestamp(timestamp: string | null): string {
  if (!timestamp) return "Timestamp unavailable";
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(timestamp));
}

function App() {
  const [calls, setCalls] = useState<IntakeCall[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [playingKey, setPlayingKey] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const activeAudio = useRef<HTMLAudioElement | null>(null);

  async function refreshCalls() {
    setLoading(true);
    setError("");
    try {
      setCalls(await loadIntakeCalls());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load recordings.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshCalls();
    return () => activeAudio.current?.pause();
  }, []);

  const visibleCalls = useMemo(
    () =>
      calls.filter((call) =>
        `${call.callerPhoneMasked} ${call.status}`.toLowerCase().includes(query.toLowerCase()),
      ),
    [calls, query],
  );

  function play(call: IntakeCall, artifactIndex: number) {
    activeAudio.current?.pause();
    const key = `${call.id}:${artifactIndex}`;
    const audio = new Audio(recordingPlaybackUrl(call.id, artifactIndex));
    activeAudio.current = audio;
    setPlayingKey(key);
    setProgress(0);
    audio.addEventListener("timeupdate", () =>
      setProgress(audio.duration ? (audio.currentTime / audio.duration) * 100 : 0),
    );
    audio.addEventListener("ended", () => setPlayingKey(null), { once: true });
    audio.addEventListener("error", () => {
      setPlayingKey(null);
      setError("Playback could not start. Confirm the local GCS credentials can read the bucket.");
    });
    void audio.play();
  }

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <p className="firm">Victus Family Law Firm</p>
          <h1>Intake listening room</h1>
          <p className="lede">
            Local review of consented voice-intake recordings. Caller identities stay masked here.
          </p>
        </div>
        <button className="refresh" onClick={() => void refreshCalls()} type="button">
          Refresh list
        </button>
      </header>

      <section className="control-bar" aria-label="Call list controls">
        <label>
          <span>Find a call</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Masked caller or status"
          />
        </label>
        <p>{calls.length} calls · newest first</p>
      </section>

      {error && <p className="message error">{error}</p>}
      {loading ? (
        <p className="message">Loading intake records…</p>
      ) : visibleCalls.length === 0 ? (
        <p className="message">No calls match this view yet.</p>
      ) : (
        <section className="call-list" aria-label="Voice intake calls">
          {visibleCalls.map((call) => (
            <article
              className={playingKey?.startsWith(`${call.id}:`) ? "call-row playing" : "call-row"}
              key={call.id}
              style={
                playingKey?.startsWith(`${call.id}:`)
                  ? ({ "--progress": `${progress}%` } as CSSProperties)
                  : undefined
              }
            >
              <div className="call-identity">
                <p className="timestamp">{formatTimestamp(call.receivedAt)}</p>
                <h2>{call.callerPhoneMasked}</h2>
              </div>
              <p className="state">{call.status.replaceAll("_", " ")}</p>
              <p className={call.recordingConsent ? "consent granted" : "consent"}>
                {call.recordingConsent ? "Consent recorded" : "No recording consent"}
              </p>
              <div className="recordings">
                {call.recordings.length ? (
                  call.recordings.map((recording) => {
                    const key = `${call.id}:${recording.index}`;
                    return (
                      <button
                        className="play"
                        key={key}
                        onClick={() => play(call, recording.index)}
                        type="button"
                      >
                        {playingKey === key ? "Playing…" : `Play ${recording.label}`}
                      </button>
                    );
                  })
                ) : (
                  <span className="no-recording">No saved audio</span>
                )}
              </div>
            </article>
          ))}
        </section>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
