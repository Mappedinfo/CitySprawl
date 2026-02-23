import type {
  CityArtifact,
  GenerateConfig,
  GenerateJobStatusResponse,
  PresetsResponse,
  StagedCityResponse,
} from '../types/city';

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

export async function generateCityV2(config: GenerateConfig): Promise<StagedCityResponse> {
  const res = await fetch(`${API_BASE}/api/v2/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  return parseJson(res);
}

export async function startGenerateCityV2Async(config: GenerateConfig): Promise<{ job_id: string; status: string }> {
  const res = await fetch(`${API_BASE}/api/v2/generate_async`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
  return parseJson(res);
}

export async function fetchGenerateJobStatus(jobId: string, sinceSeq = 0): Promise<GenerateJobStatusResponse> {
  const url = new URL(`${API_BASE}/api/v2/jobs/${jobId}`);
  if (sinceSeq > 0) url.searchParams.set('since_seq', String(sinceSeq));
  const res = await fetch(url.toString());
  return parseJson(res);
}

export async function fetchGenerateJobResult(jobId: string): Promise<StagedCityResponse> {
  const res = await fetch(`${API_BASE}/api/v2/jobs/${jobId}/result`);
  return parseJson(res);
}

export async function loadStagedJson(path: string): Promise<StagedCityResponse> {
  const res = await fetch(`${API_BASE}/api/v2/load_staged_json`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  return parseJson(res);
}
