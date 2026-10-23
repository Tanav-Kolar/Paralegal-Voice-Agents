export type Recording = {
  index: number;
  label: string;
};

export type IntakeCall = {
  id: string;
  status: string;
  callerPhoneMasked: string;
  receivedAt: string | null;
  recordingConsent: boolean;
  recordings: Recording[];
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

export type AccessState = { enabled: boolean; authenticated: boolean };

export async function loadAccess(): Promise<AccessState> {
  const response = await fetch(`${apiBaseUrl}/api/v1/access/me`);
  if (!response.ok) throw new Error("The service is unavailable.");
  return response.json() as Promise<AccessState>;
}

export async function login(code: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/v1/access/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (response.status === 429) throw new Error("Too many attempts. Try again in a few minutes.");
  if (!response.ok) throw new Error("That access code is not valid.");
}

export type DemoSession = { intakeCallId: string; streamPath: string };

export async function startDemoSession(): Promise<DemoSession> {
  const response = await fetch(`${apiBaseUrl}/api/v1/demo/session`, { method: "POST" });
  if (response.status === 401) throw new Error("Your access expired. Reload the page and sign in again.");
  if (!response.ok) throw new Error("The demo could not start a call. Try again.");
  return response.json() as Promise<DemoSession>;
}
