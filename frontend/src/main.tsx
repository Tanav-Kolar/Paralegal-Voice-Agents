import { type FormEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { type AccessState, type PortalUser, loadAccess, login, portalLogin, portalLogout, portalMe } from "./api";
import { CallPanel } from "./CallPanel";
import { ReviewList } from "./ReviewList";
import "./styles.css";

type View = "portal" | "demo";

function App() {
  const [user, setUser] = useState<PortalUser | null>(null);
  const [access, setAccess] = useState<AccessState | null>(null);
  const [view, setView] = useState<View>(
    () => new URLSearchParams(window.location.search).get("view") === "demo" ? "demo" : "portal",
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void Promise.allSettled([portalMe(), loadAccess()]).then(([portal, demo]) => {
      if (portal.status === "fulfilled") setUser(portal.value);
      if (demo.status === "fulfilled") setAccess(demo.value);
      setLoading(false);
    });
  }, []);

  async function signIn(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { setUser(await portalLogin(email, password)); setPassword(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Sign-in failed."); }
    finally { setBusy(false); }
  }
  async function enterDemo(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { await login(code); setAccess(await loadAccess()); setCode(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Access code failed."); }
    finally { setBusy(false); }
  }
  async function signOut() { await portalLogout(); setUser(null); setView("portal"); }

  return <main className="shell">
    <header className="masthead"><div className="brand"><span className="brand-mark" aria-hidden="true">V</span><div><strong>Victus</strong><small>Family Law Firm</small></div></div><div className="masthead-right"><span>Intake workspace</span>{user && <><span className="user-name">{user.name} · {user.role}</span><button className="text-button" onClick={() => void signOut()}>Sign out</button></>}</div></header>
    {loading ? <p className="message">Loading workspace…</p> : <>
      {access?.enabled && <nav className="tabs" aria-label="Workspace sections"><button className={view === "portal" ? "active" : ""} onClick={() => { setView("portal"); setError(""); }}>Lawyer portal</button><button className={view === "demo" ? "active" : ""} onClick={() => { setView("demo"); setError(""); }}>Try the agent</button></nav>}
      {view === "demo" && access?.enabled ? access.authenticated ? <div className="demo-area"><CallPanel onFinished={() => setView("portal")} /></div> : <section className="login-wrap"><h1>Try the intake agent</h1><p>Enter the demo access code to start a test call.</p><form onSubmit={(e) => void enterDemo(e)}><label className="field">Access code<input type="password" value={code} onChange={(e) => setCode(e.target.value)} required /></label><button className="primary" disabled={busy}>Continue</button></form>{error && <p className="message error" role="alert">{error}</p>}</section>
      : user ? <ReviewList user={user} /> : <section className="login-wrap"><div className="login-title"><h1>Review with context.</h1><p>Sign in to read intake summaries, listen to consented calls, and record your review.</p></div><form onSubmit={(e) => void signIn(e)}><h2>Lawyer portal sign-in</h2><label className="field">Work email<input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></label><label className="field">Password<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label><button className="primary" disabled={busy}>Sign in</button>{error && <p className="message error" role="alert">{error}</p>}</form></section>}
    </>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
