import { type FormEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { type AccessState, loadAccess, login } from "./api";
import { CallPanel } from "./CallPanel";
import { ReviewList } from "./ReviewList";
import "./styles.css";

type View = "try" | "review";

function AccessGate({ onSignedIn }: { onSignedIn: () => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await login(code);
      onSignedIn();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign-in failed.");
    }
  }

  return (
    <form className="gate" onSubmit={(event) => void submit(event)}>
      <label>
        <span>Access code</span>
        <input
          autoFocus
          type="password"
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
      </label>
      <button className="play" type="submit">
        Continue
      </button>
      {error && <p className="message error">{error}</p>}
    </form>
  );
}

function App() {
  const [access, setAccess] = useState<AccessState | null>(null);
  const [failed, setFailed] = useState(false);
  const [view, setView] = useState<View>("review");

  async function refreshAccess() {
    try {
      const next = await loadAccess();
      setAccess(next);
      setView(next.enabled ? "try" : "review");
    } catch {
      setFailed(true);
    }
  }

  useEffect(() => {
    void refreshAccess();
  }, []);

  const needsCode = access?.enabled && !access.authenticated;

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <p className="firm">Victus Family Law Firm</p>
          <h1>Intake listening room</h1>
          <p className="lede">
            Consent-first voice intake for family-law enquiries. Caller identities stay masked here.
          </p>
        </div>
      </header>

      {failed && <p className="message error">The service is unavailable. Reload to try again.</p>}
      {needsCode && <AccessGate onSignedIn={() => void refreshAccess()} />}
      {access && !needsCode && (
        <>
          <nav className="tabs" aria-label="Sections">
            {access.enabled && (
              <button
                aria-current={view === "try" ? "page" : undefined}
                onClick={() => setView("try")}
                type="button"
              >
                Try the agent
              </button>
            )}
            <button
              aria-current={view === "review" ? "page" : undefined}
              onClick={() => setView("review")}
              type="button"
            >
              Reviewed calls
            </button>
          </nav>
          {view === "try" ? <CallPanel onFinished={() => setView("review")} /> : <ReviewList />}
        </>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
