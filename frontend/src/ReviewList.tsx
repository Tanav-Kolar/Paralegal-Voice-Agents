import { useEffect, useMemo, useState } from "react";
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
const elapsed = (milliseconds: number) => {
  const seconds = Math.floor(milliseconds / 1000);
  return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
};
type Fact = CallDetail["summary"]["facts"][number];
const factSections = [
  { title: "People and location", fields: ["Caller name", "Caller location", "Other people named", "city"] },
  { title: "Relationships and care", fields: ["Relationship and marriage", "Children and care", "Housing and living arrangements"] },
  { title: "Property and finances", fields: ["Property, assets and debts", "Financial support", "Estate and will"] },
  { title: "Procedure and next steps", fields: ["Timeline of events", "Court proceedings and orders", "Documents and evidence", "Urgent concerns or deadlines", "Help the caller wants"] },
];
function groupFacts(facts: Fact[]) {
  const assigned = new Set<string>();
  const groups = factSections.map((section) => ({
    title: section.title,
    facts: facts.filter((fact) => {
      const matches = section.fields.some((field) => field.toLowerCase() === fact.field.toLowerCase());
      if (matches) assigned.add(fact.field);
      return matches;
    }),
  })).filter((section) => section.facts.length > 0);
  const other = facts.filter((fact) => !assigned.has(fact.field) && fact.field.toLowerCase() !== "matter type");
  if (other.length) groups.push({ title: "Other call details", facts: other });
  return groups;
}
function factValue(fact: Fact) {
  return fact.value || (fact.notProvided ? "Not provided" : "Content unavailable");
}

export function ReviewList({ user }: { user: PortalUser }) {
  const [section, setSection] = useState<"intakes" | "sessions">("intakes");
  const [sessions, setSessions] = useState<CallSession[]>([]);
  const [calls, setCalls] = useState<IntakeCall[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<CallDetail | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [transcriptQuery, setTranscriptQuery] = useState("");
  const [status, setStatus] = useState("pending");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const next = await loadIntakeCalls();
      setCalls(next);
      setSelected((current) => current && next.some((call) => call.id === current) ? current : next[0]?.id ?? null);
      setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load calls."); }
  }
  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    if (!selected) { setDetail(null); return; }
    let active = true;
    setDetail(null);
    setTranscriptQuery("");
    void loadCallDetail(selected).then((next) => {
      if (!active) return;
      setDetail(next); setStatus(next.reviewStatus); setNotes(next.notes);
      setError(""); setMessage("");
    }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Could not load call."); });
    return () => { active = false; };
  }, [selected]);
  const visible = useMemo(() => calls.filter((call) =>
    (filter === "all" || (call.reviewStatus ?? "pending") === filter) &&
    (call.callerPhoneMasked + " " + (call.matterType ?? "") + " " + call.status).toLowerCase().includes(query.toLowerCase()),
  ), [calls, filter, query]);
  const current = calls.find((call) => call.id === selected);
  const selectedCallerName = detail?.summary.facts.find((fact) => fact.field.toLowerCase() === "caller name")?.value;
  const transcript = useMemo(() => detail?.transcript.filter((item) =>
    item.text.toLowerCase().includes(transcriptQuery.trim().toLowerCase()),
  ) ?? [], [detail, transcriptQuery]);

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
      setDetail(next); setMessage("Brief version " + report.version + " created.");
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not create brief."); }
    finally { setBusy(false); }
  }

  return <>
    <nav className="review-nav" aria-label="Review views">
      <button className={section === "intakes" ? "active" : ""} onClick={() => setSection("intakes")}>Intake queue</button>
      <button className={section === "sessions" ? "active" : ""} onClick={() => void showSessions()}>Call sessions</button>
    </nav>
    {section === "sessions" ? <section className="event-page">
      <header><h2>Call sessions</h2><p>Events from the same call are grouped together. Older sessions cannot always be linked to an intake record.</p></header>
      {error && <p className="message error" role="alert">{error}</p>}
      {sessions.length ? <div className="session-list">{sessions.map((session) => <details className="session" key={session.id}>
        <summary><span className="session-heading"><strong>{date(session.startedAt)}</strong><small>Call session</small></span><span className="session-meta"><span>{session.events.length} {session.events.length === 1 ? "event" : "events"}</span><small>Last event: {eventLabel(session.lastEventType)}</small></span><span className="session-chevron" aria-hidden="true" /></summary>
        <ol className="session-timeline">{session.events.map((event, index) => <li key={event.type + index}><span className="timeline-dot" aria-hidden="true" /><span className="timeline-content"><strong>{eventLabel(event.type)}</strong><small>{date(event.receivedAt)}</small></span></li>)}</ol>
      </details>)}</div> : !error && <p className="empty">No call sessions were saved.</p>}
    </section> : <div className="review-workspace">
      <aside className="review-queue" aria-label="Intake queue"><div className="review-queue-inner">
        <div className="queue-head"><h2>Intake queue</h2><button className="text-button" onClick={() => void refresh()}>Refresh</button></div>
        <label className="sr-only" htmlFor="case-search">Search cases</label>
        <input id="case-search" className="queue-search" placeholder="Search cases" value={query} onChange={(event) => setQuery(event.target.value)} />
        <div className="queue-filters" role="group" aria-label="Filter by review status">
          {["all", "pending", "reviewed"].map((value) => <button type="button" key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{value === "all" ? "All" : statusLabels[value]}</button>)}
        </div>
        <p className="queue-count">{visible.length} {visible.length === 1 ? "case" : "cases"}</p>
        <div className="queue-items">{visible.map((call) => <button type="button" key={call.id} className={"queue-item " + (selected === call.id ? "selected" : "")} aria-current={selected === call.id ? "true" : undefined} onClick={() => setSelected(call.id)}>
          <span className="queue-item-top"><strong>{call.callerName || call.callerPhoneMasked}</strong><small>{date(call.receivedAt)}</small></span>
          <span className="queue-matter">{call.matterType || "Matter not identified"}</span>
          <span className="queue-item-bottom"><em className={"status status-" + (call.reviewStatus || "pending")}>{statusLabels[call.reviewStatus || "pending"] || "Needs review"}</em><small>{call.reportCount || 0} briefs</small></span>
        </button>)}</div>
        {!visible.length && <p className="empty">No cases match this view.</p>}
      </div></aside>
      <main className="review-case" aria-label="Selected intake">
        {error && <p className="message error" role="alert">{error}</p>}
        {!selected ? <div className="empty">Select an intake to review.</div> : !detail || !current ? <p className="message">Loading intake…</p> : <>
          <div className="case-breadcrumb">Intake queue <span>/</span> Case review</div>
          <header className="case-head"><div><h1>{detail.summary.matterType || "Matter not identified"}</h1><p>{selectedCallerName ? selectedCallerName + " · " : ""}{current.callerPhoneMasked} · Received {date(current.receivedAt)}</p></div><span className={"review-pill " + (status === "reviewed" ? "reviewed" : "")}>{statusLabels[status] || "Needs review"}</span></header>
          <div className="case-meta"><span>Case ID <strong>{current.id.slice(0, 8)}</strong></span><span>Language <strong>{detail.summary.language || "Not recorded"}</strong></span><span>Recording consent <strong>{current.recordingConsent ? "Granted" : "Not granted"}</strong></span></div>
          {detail.summary.contentUnavailable && <p className="message error" role="status">Some encrypted call content is unavailable. Restore the original FIELD_ENCRYPTION_KEY to view it.</p>}
          <section className="review-section" aria-labelledby="triage-title"><div className="review-section-head"><h2 id="triage-title">Triage summary</h2><small>Third-person case account</small></div><p className="triage-copy">{detail.summary.overview}</p></section>
          <section className="review-section" aria-labelledby="fact-title"><div className="review-section-head"><h2 id="fact-title">Case Fact Sheet</h2><small>Structured intake record</small></div>
            <table className="fact-table"><caption className="sr-only">Structured facts from the selected intake call</caption>
              <colgroup><col className="fact-label-column" /><col /></colgroup>
              <thead><tr><th scope="col">Case detail</th><th scope="col">Case information</th></tr></thead>
              <tbody><tr className="fact-table-group"><th colSpan={2} scope="rowgroup">Classification</th></tr><tr><th scope="row">Matter type</th><td><strong>{detail.summary.matterType || "Not identified"}</strong><small>{detail.summary.matterType ? "Identified from the recorded call" : "The call did not establish a category"}</small></td></tr></tbody>
              {groupFacts(detail.summary.facts).map((group) => <tbody key={group.title}><tr className="fact-table-group"><th colSpan={2} scope="rowgroup">{group.title}</th></tr>{group.facts.map((fact, index) => <tr key={fact.field + index}><th scope="row">{fact.field.replaceAll("_", " ")}</th><td className={fact.notProvided ? "not-provided" : ""}>{factValue(fact)}{fact.providedByCaller === false && fact.value && <small>Manually corrected</small>}</td></tr>)}</tbody>)}
              {!detail.summary.facts.length && <tbody><tr><td colSpan={2} className="fact-table-empty">No structured facts were saved for this call.</td></tr></tbody>}
            </table>
          </section>
          <div className="case-bottom">
            <section className="review-section" aria-labelledby="review-title"><div className="review-section-head"><h2 id="review-title">Review</h2></div><label className="field">Status<select value={status} onChange={(event) => setStatus(event.target.value)}>{statuses.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label className="field">Internal notes<textarea rows={4} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Add context for the next reviewer" /></label><button className="primary" disabled={busy} onClick={() => void save()}>Save review</button></section>
            <section className="review-section" aria-labelledby="brief-title"><div className="review-section-head"><h2 id="brief-title">PDF briefs</h2></div>{user.role !== "reviewer" && <button className="secondary" disabled={busy} onClick={() => void createBrief()}>Create new brief</button>}{detail.reports.length ? <ul className="reports">{[...detail.reports].reverse().map((report) => <li key={report.version}><span>Version {report.version}<small>{date(report.generatedAt)}</small></span><a href={briefUrl(selected, report.version)}>Download PDF</a></li>)}</ul> : <p className="empty-inline">No brief created yet.</p>}</section>
          </div>
          {message && <p className="message success" role="status">{message}</p>}
        </>}
      </main>
      <aside className="record-pane" aria-label="Call recording and transcript">
        {selected && detail && current ? <div className="record-pane-inner"><header><h2>Call transcript</h2><p>Read alongside the fact sheet</p></header>
          <div className="record-tools"><h3>Call recording</h3>{current.recordings.length ? current.recordings.map((recording) => <div className="recording" key={recording.index}><span>{recording.label}</span><audio controls preload="none" src={recordingPlaybackUrl(selected, recording.index)} onError={() => setError("Recording playback is unavailable.")}>Your browser cannot play this recording.</audio></div>) : <p className="empty-inline">No saved recording.</p>}
            <label className="sr-only" htmlFor="transcript-search">Search transcript</label><input id="transcript-search" placeholder="Find in transcript" value={transcriptQuery} onChange={(event) => setTranscriptQuery(event.target.value)} /></div>
          <div className="record-transcript">{transcript.length ? <ol>{transcript.map((item) => <li key={item.id || item.startedAtMs + item.text}><div><strong className={item.speaker}>{item.speaker === "agent" ? "Agent" : "Caller"}</strong><time>{elapsed(item.startedAtMs)}</time></div><p>{item.text}</p></li>)}</ol> : <p className="empty-inline">{detail.transcript.length ? "No transcript lines match your search." : "No consented transcript was saved."}</p>}</div><div className="record-footer">Final speech-recognition text</div>
        </div> : <div className="record-pane-empty">Select a case to read its transcript.</div>}
      </aside>
    </div>}
  </>;
}
