import { useEffect, useMemo, useRef, useState } from "react";
import {
  type CallDetail, type CallSession, type IntakeCall, type PortalUser, briefUrl, generateBrief,
  loadCallDetail, loadCallSessions, loadIntakeCalls, recordingPlaybackUrl, saveCallReview,
} from "./api";

const statusLabels: Record<string, string> = {
  pending: "Needs review", reviewed: "Reviewed", callback_attempted: "Callback attempted",
  engagement_declined: "Engagement declined", engaged_off_platform: "Engaged off platform",
};
const statuses = Object.entries(statusLabels);
const eventLabel = (type: string) => type.replace(/^voicebot_/, "").replaceAll("_", " ");
const date = (value: string | null) => value
  ? new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value))
  : "Time unavailable";

export function ReviewList({ user }: { user: PortalUser }) {
  const [section, setSection] = useState<"intakes" | "sessions">("intakes");
  const [sessions, setSessions] = useState<CallSession[]>([]);
  const [calls, setCalls] = useState<IntakeCall[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<CallDetail | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [status, setStatus] = useState("pending");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);

  async function refresh() {
    try {
      const next = await loadIntakeCalls();
      setCalls(next);
      setSelected((current) => current && next.some((call) => call.id === current) ? current : next[0]?.id ?? null);
      setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load calls."); }
  }
  useEffect(() => { void refresh(); return () => audio.current?.pause(); }, []);
  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    setDetail(null);
    void loadCallDetail(selected).then((next) => {
      setDetail(next); setStatus(next.reviewStatus); setNotes(next.notes);
      setError(""); setMessage("");
    }).catch((reason) => setError(reason instanceof Error ? reason.message : "Could not load call."));
  }, [selected]);
  const visible = useMemo(() => calls.filter((call) =>
    (filter === "all" || (call.reviewStatus ?? "pending") === filter) &&
    `${call.callerPhoneMasked} ${call.matterType ?? ""} ${call.status}`.toLowerCase().includes(query.toLowerCase()),
  ), [calls, filter, query]);
  const current = calls.find((call) => call.id === selected);

  async function showSessions() {
    setSection("sessions"); setError("");
    try { setSessions(await loadCallSessions()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load call sessions."); }
  }

  async function save() {
    if (!selected) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await saveCallReview(selected, status, notes);
      setMessage("Review saved.");
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not save review."); }
    finally { setBusy(false); }
  }
  async function createBrief() {
    if (!selected) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const report = await generateBrief(selected);
      const next = await loadCallDetail(selected);
      setDetail(next); setMessage(`Brief version ${report.version} created.`);
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not create brief."); }
    finally { setBusy(false); }
  }
  function toggleAudio(index: number) {
    if (!selected) return;
    const key = `${selected}:${index}`;
    if (playing === key) { audio.current?.pause(); setPlaying(null); return; }
    audio.current?.pause();
    const next = new Audio(recordingPlaybackUrl(selected, index));
    audio.current = next; setPlaying(key);
    next.addEventListener("ended", () => setPlaying(null), { once: true });
    next.addEventListener("error", () => { setPlaying(null); setError("Recording playback is unavailable."); }, { once: true });
    void next.play().catch(() => { setPlaying(null); setError("Recording playback is unavailable."); });
  }

  return <><nav className="review-nav" aria-label="Review views"><button className={section === "intakes" ? "active" : ""} onClick={() => setSection("intakes")}>Intake queue</button><button className={section === "sessions" ? "active" : ""} onClick={() => void showSessions()}>Call sessions</button></nav>
  {section === "sessions" ? <section className="event-page"><header><h2>Call sessions</h2><p>Events from the same call stream are grouped together. Older sessions cannot be matched reliably to an intake record.</p></header>{error && <p className="message error" role="alert">{error}</p>}{sessions.length ? <div className="session-list">{sessions.map((session) => <details className="session" key={session.id}><summary><span className="session-heading"><strong>{date(session.startedAt)}</strong><small>Call session</small></span><span className="session-meta"><span>{session.events.length} {session.events.length === 1 ? "event" : "events"}</span><small>Last event: {eventLabel(session.lastEventType)}</small></span><span className="session-chevron" aria-hidden="true" /></summary><ol className="session-timeline">{session.events.map((event, index) => <li key={`${event.type}-${index}`}><span className="timeline-dot" aria-hidden="true" /><span className="timeline-content"><strong>{eventLabel(event.type)}</strong><small>{date(event.receivedAt)}</small></span></li>)}</ol></details>)}</div> : !error && <p className="empty">No call sessions were saved.</p>}</section> : <div className="workspace">
    <aside className="queue" aria-label="Intake queue">
      <div className="queue-head"><h2>Intake queue</h2><button className="text-button" onClick={() => void refresh()}>Refresh</button></div>
      <div className="queue-controls">
        <input aria-label="Find a call" placeholder="Find by caller or matter" value={query} onChange={(e) => setQuery(e.target.value)} />
        <select aria-label="Filter review status" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">All statuses</option>{statuses.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>
      <p className="queue-count">{visible.length} {visible.length === 1 ? "intake" : "intakes"}</p>
      <div className="queue-items">{visible.map((call) => <button type="button" key={call.id}
        className={`queue-item ${selected === call.id ? "selected" : ""}`} onClick={() => setSelected(call.id)}>
        <span className="queue-item-top"><strong>{call.callerPhoneMasked}</strong><small>{date(call.receivedAt)}</small></span>
        <span>{call.matterType || "Matter not identified"}</span>
        <span className="queue-item-bottom"><em className={`status status-${call.reviewStatus || "pending"}`}>{statusLabels[call.reviewStatus || "pending"] || "Needs review"}</em><small>{call.reportCount || 0} briefs</small></span>
      </button>)}</div>
      {!visible.length && <p className="empty">No intakes match this view.</p>}
    </aside>
    <section className="case" aria-label="Selected intake">
      {error && <p className="message error" role="alert">{error}</p>}
      {!selected ? <div className="empty">Select an intake to review.</div> : !detail || !current ? <p className="message">Loading intake…</p> : <>
        <header className="case-head"><div><p className="case-date">Received {date(current.receivedAt)}</p><h2>{current.callerPhoneMasked}</h2><p>{detail.summary.matterType || "Matter not identified"} · {current.status.replaceAll("_", " ")}</p></div><span className="case-id">ID {current.id.slice(0, 8)}</span></header>
        {detail.summary.contentUnavailable && <p className="message error" role="status">Some encrypted caller content cannot be read with the configured field key. Restore the original FIELD_ENCRYPTION_KEY to view it and generate a complete brief.</p>}
        <div className="case-grid">
          <section className="case-section"><h3>Triage summary</h3><p className="helper">Caller statements and saved facts. Verify before relying on them.</p>
            <p className="overview">{detail.summary.overview}</p>
            <dl className="facts"><div><dt>Preferred language</dt><dd>{detail.summary.language || "Not recorded"}</dd></div><div><dt>Recording consent</dt><dd>{current.recordingConsent ? "Granted" : "Not granted"}</dd></div></dl>
            {detail.summary.facts.length > 0 && <><h4>Recorded facts</h4><dl className="facts">{detail.summary.facts.map((fact, index) => <div key={index}><dt>{fact.field.replaceAll("_", " ")}</dt><dd>{fact.value || (fact.notProvided ? "Not provided" : "Encrypted value unavailable")}</dd></div>)}</dl></>}
            <h4>Caller statements</h4>{detail.summary.callerStatements.length ? <ol className="statements">{detail.summary.callerStatements.map((item, index) => <li key={item.id || index}>{item.text}</li>)}</ol> : <p className="empty-inline">{detail.summary.contentUnavailable ? "Encrypted caller statements are unavailable." : "No caller statements were saved."}</p>}
          </section>
          <div className="case-side"><section className="case-section"><h3>Review</h3><label className="field">Status<select value={status} onChange={(e) => setStatus(e.target.value)}>{statuses.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label className="field">Internal notes<textarea rows={6} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Add context for the next reviewer" /></label><button className="primary" disabled={busy} onClick={() => void save()}>Save review</button></section>
          <section className="case-section"><h3>Recording</h3>{current.recordings.length ? current.recordings.map((recording) => <button key={recording.index} className="audio-button" onClick={() => toggleAudio(recording.index)}>{playing === `${selected}:${recording.index}` ? "Pause" : "Play"} {recording.label}</button>) : <p className="empty-inline">No saved recording.</p>}</section>
          <section className="case-section"><h3>Call events</h3>{detail.events.length ? <ol className="event-list">{detail.events.map((event, index) => <li key={`${event.type}-${index}`}><span>{event.type.replace(/^voicebot_/, "").replaceAll("_", " ")}</span><small>{date(event.receivedAt)}</small></li>)}</ol> : <p className="empty-inline">No call events were saved.</p>}</section>
          <section className="case-section"><h3>PDF briefs</h3><p className="helper">Each generated version is kept separately.</p>{user.role !== "reviewer" && <button className="secondary" disabled={busy} onClick={() => void createBrief()}>Create new brief</button>}{detail.reports.length ? <ul className="reports">{[...detail.reports].reverse().map((report) => <li key={report.version}><span>Version {report.version}<small>{date(report.generatedAt)}</small></span><a href={briefUrl(selected, report.version)}>Download PDF</a></li>)}</ul> : <p className="empty-inline">No brief created yet.</p>}</section></div>
        </div><p className="disclaimer">{detail.summary.disclaimer}</p>{message && <p className="message success" role="status">{message}</p>}
      </>}
    </section>
  </div>}</>;
}
