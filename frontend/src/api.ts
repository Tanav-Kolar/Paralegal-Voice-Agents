export type Recording = {
  index: number;
  label: string;
};

export type IntakeCall = {
  id: string;
  status: string;
  callerPhoneMasked: string;
  callerName?: string | null;
  receivedAt: string | null;
  recordingConsent: boolean;
  recordings: Recording[];
  matterType?: string | null;
  reviewStatus?: string;
  reportCount?: number;
};

const apiBaseUrl = import.meta.env.VITE_REVIEW_API_BASE_URL ?? "";

export async function loadIntakeCalls(): Promise<IntakeCall[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/review/intake-calls`);
  if (!response.ok) {
    throw new Error("The local review API is unavailable.");
  }
  return response.json() as Promise<IntakeCall[]>;
}

export function recordingPlaybackUrl(callId: string, artifactIndex: number): string {
  return `${apiBaseUrl}/api/v1/review/intake-calls/${callId}/recordings/${artifactIndex}/playback`;
}

export type DemoSession = { intakeCallId: string; streamPath: string; sampleRate: number };

export async function startDemoSession(): Promise<DemoSession> {
  const response = await fetch(`${apiBaseUrl}/api/v1/demo/session`, { method: "POST" });
  if (response.status === 401) throw new Error("Your session expired. Reload the page and sign in again.");
  if (!response.ok) throw new Error("The demo could not start a call. Try again.");
  return response.json() as Promise<DemoSession>;
}

export type PortalUser = { email: string; name: string; role: "admin" | "lawyer" | "reviewer" | "demo_lawyer" };
export type TriageSummary = {
  matterType: string | null;
  language: string | null;
  overview: string;
  contentUnavailable: boolean;
  callerStatements: { id: string; text: string }[];
  facts: { field: string; value: string | null; providedByCaller?: boolean; notProvided: boolean; sourceIds: string[] }[];
  disclaimer: string;
};
export type CallDetail = {
  id: string;
  summary: TriageSummary;
  reviewStatus: string;
  notes: string;
  transcript: {
    id: string;
    speaker: "agent" | "caller";
    text: string;
    startedAtMs: number;
    endedAtMs: number;
    language: string | null;
  }[];
  events: { type: string; receivedAt: string | null }[];
  reports: { version: number; generatedAt: string | null }[];
};

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status}).`);
  }
  return response.json() as Promise<T>;
}
export const portalMe = () => json<PortalUser>("/api/v1/portal/me");
export const portalLogin = (email: string, password: string) =>
  json<PortalUser>("/api/v1/portal/login", { method: "POST", body: JSON.stringify({ email, password }) });
export const portalLogout = () => json<{ authenticated: boolean }>("/api/v1/portal/logout", { method: "POST" });
export const loadCallDetail = (id: string) => json<CallDetail>(`/api/v1/review/intake-calls/${id}`);
export type CallEvent = { type: string; receivedAt: string | null };
export type CallSession = {
  id: string;
  startedAt: string | null;
  lastAt: string | null;
  lastEventType: string;
  events: CallEvent[];
};
export const loadCallSessions = () => json<CallSession[]>("/api/v1/review/events");
export const saveCallReview = (id: string, status: string, notes: string) =>
  json<{ saved: boolean }>(`/api/v1/review/intake-calls/${id}/review`, {
    method: "PUT", body: JSON.stringify({ status, notes }),
  });
export const generateBrief = (id: string) =>
  json<{ version: number }>(`/api/v1/review/intake-calls/${id}/reports`, { method: "POST" });
export const briefUrl = (id: string, version: number) =>
  `${apiBaseUrl}/api/v1/review/intake-calls/${id}/reports/${version}/download`;
