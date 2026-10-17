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
