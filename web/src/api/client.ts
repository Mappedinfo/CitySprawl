import type { CityArtifact, GenerateConfig, PresetsResponse, StagedCityResponse } from '../types/city';

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000';

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function fetchHealth(): Promise<{ status: string }> {
  const res = await fetch(`${API_BASE}/api/v1/health`);
  return parseJson(res);
}

export async function fetchPresets(): Promise<PresetsResponse> {
  const res = await fetch(`${API_BASE}/api/v1/presets`);
  return parseJson(res);
}

export async function generateCity(config: GenerateConfig): Promise<CityArtifact> {
  const res = await fetch(`${API_BASE}/api/v1/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  return parseJson(res);
}

export async function generateCityStaged(config: GenerateConfig): Promise<StagedCityResponse> {
  const res = await fetch(`${API_BASE}/api/v1/generate_staged`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  return parseJson(res);
}
