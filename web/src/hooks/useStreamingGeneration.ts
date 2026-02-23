import { useCallback, useRef, useState } from 'react';

import type { GenerateConfig, Point2D, StagedCityResponse } from '../types/city';

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000';

// Stream event types
export type StreamEvent =
  | { event_type: 'road_node_added'; data: { id: string; x: number; y: number; kind: string } }
  | {
      event_type: 'road_edge_added';
      data: { id: string; u: string; v: string; road_class: string; length_m?: number };
    }
  | {
      event_type: 'road_trace_progress';
      data: {
        trace_id: string;
        points: Point2D[];
        complete: boolean;
        road_class?: string;
        culdesac?: boolean;
      };
    }
  | { event_type: 'progress'; data: { phase: string; progress: number; message: string } }
  | { event_type: 'stage_complete'; data: { stage_id: string } }
  | { event_type: 'terrain_milestone'; data: { stage: string; resolution?: number; extent_m?: number } }
  | { event_type: 'river_progress'; data: { river_id: string; centerline: Point2D[]; flow: number } };

// Incremental state for real-time rendering
export type IncrementalState = {
  nodes: Map<string, { id: string; x: number; y: number; kind: string }>;
  edges: Map<string, { id: string; u: string; v: string; road_class: string; length_m?: number }>;
  partialTraces: Map<string, Point2D[]>;
  completedTraces: Array<{ trace_id: string; points: Point2D[]; road_class?: string; culdesac?: boolean }>;
  rivers: Array<{ river_id: string; centerline: Point2D[]; flow: number }>;
};

export type StreamingProgress = {
  phase: string;
  progress: number;
  message: string;
};

export type StreamingStatus = 'idle' | 'connecting' | 'streaming' | 'completed' | 'error';

function createEmptyState(): IncrementalState {
  return {
    nodes: new Map(),
    edges: new Map(),
    partialTraces: new Map(),
    completedTraces: [],
    rivers: [],
  };
}

function mergeEvent(state: IncrementalState, event: StreamEvent): IncrementalState {
  const next = { ...state };

  switch (event.event_type) {
    case 'road_node_added':
      next.nodes = new Map(state.nodes);
      next.nodes.set(event.data.id, event.data);
      break;

    case 'road_edge_added':
      next.edges = new Map(state.edges);
      next.edges.set(event.data.id, event.data);
      break;

    case 'road_trace_progress':
      if (event.data.complete) {
        next.partialTraces = new Map(state.partialTraces);
        next.partialTraces.delete(event.data.trace_id);
        next.completedTraces = [
          ...state.completedTraces,
          {
            trace_id: event.data.trace_id,
            points: event.data.points,
            road_class: event.data.road_class,
            culdesac: event.data.culdesac,
          },
        ];
      } else {
        next.partialTraces = new Map(state.partialTraces);
        next.partialTraces.set(event.data.trace_id, event.data.points);
      }
      break;

    case 'river_progress':
      next.rivers = [...state.rivers, { river_id: event.data.river_id, centerline: event.data.centerline, flow: event.data.flow }];
      break;

    case 'progress':
    case 'stage_complete':
    case 'terrain_milestone':
      break;
  }

  return next;
}

function isStagedCityResponse(value: unknown): value is StagedCityResponse {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Record<string, unknown>;
  return !!candidate.final_artifact && typeof candidate.final_artifact === 'object' && Array.isArray(candidate.stages);
}

function splitSseBlocks(buffer: string): { blocks: string[]; remainder: string } {
  let normalized = buffer.replace(/\r\n/g, '\n');
  normalized = normalized.replace(/\r/g, '\n');
  const parts = normalized.split('\n\n');
  return { blocks: parts.slice(0, -1), remainder: parts[parts.length - 1] ?? '' };
}

export function useStreamingGeneration() {
  const [state, setState] = useState<IncrementalState>(createEmptyState());
  const [progress, setProgress] = useState<StreamingProgress>({ phase: '', progress: 0, message: '' });
  const [status, setStatus] = useState<StreamingStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StagedCityResponse | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeRequestIdRef = useRef(0);

  const abortActiveRequest = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    abortActiveRequest();
    setState(createEmptyState());
    setProgress({ phase: '', progress: 0, message: '' });
    setStatus('idle');
    setError(null);
    setResult(null);
  }, [abortActiveRequest]);

  const stop = useCallback(() => {
    abortActiveRequest();
    setStatus('idle');
  }, [abortActiveRequest]);

  const start = useCallback(
    (config: GenerateConfig): Promise<StagedCityResponse | null> => {
      return new Promise((resolve, reject) => {
        const requestId = activeRequestIdRef.current + 1;
        activeRequestIdRef.current = requestId;

        reset();
        setStatus('connecting');

        const controller = new AbortController();
        abortRef.current = controller;
        const isCurrent = () => activeRequestIdRef.current === requestId;

        const url = `${API_BASE}/api/v2/generate_stream`;

        const handleMessage = (eventName: string, dataText: string): StagedCityResponse | null => {
          let parsed: unknown;
          try {
            parsed = JSON.parse(dataText);
          } catch {
            return null;
          }

          const data = parsed as Record<string, unknown>;

          if (eventName === 'heartbeat' || data.status === 'connected' || data.status === 'generating') {
            if (isCurrent()) setStatus('streaming');
            return null;
          }

          if (Array.isArray(data.events)) {
            if (isCurrent()) setStatus('streaming');
            for (const item of data.events) {
              const event = item as StreamEvent;
              if (event.event_type === 'progress') {
                if (isCurrent()) setProgress(event.data);
              } else {
                if (isCurrent()) setState((prev) => mergeEvent(prev, event));
              }
            }
          }

          if (eventName === 'complete' || data.stream_complete === true) {
            if (isStagedCityResponse(parsed)) return parsed;
            if (data.result && isStagedCityResponse(data.result)) return data.result;
          }

          if (data.error || eventName === 'error') {
            const errorMsg = String(data.error ?? 'Unknown error');
            if (isCurrent()) {
              setError(errorMsg);
              setStatus('error');
            }
            throw new Error(errorMsg);
          }

          return null;
        };

        fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
          },
          body: JSON.stringify(config),
          signal: controller.signal,
        })
          .then(async (response) => {
            if (!response.ok) throw new Error(`HTTP error: ${response.status}`);

            const reader = response.body?.getReader();
            if (!reader) throw new Error('No response body');

            const decoder = new TextDecoder();
            let rawBuffer = '';
            let finalResult: StagedCityResponse | null = null;

            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              if (!isCurrent()) return;
              if (controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');

              rawBuffer += decoder.decode(value, { stream: true });
              const { blocks, remainder } = splitSseBlocks(rawBuffer);
              rawBuffer = remainder;

              for (const block of blocks) {
                if (!block.trim()) continue;
                let eventName = '';
                const dataLines: string[] = [];
                for (const rawLine of block.split('\n')) {
                  const line = rawLine.trimEnd();
                  if (!line || line.startsWith(':')) continue;
                  if (line.startsWith('event:')) {
                    eventName = line.slice(6).trim();
                    continue;
                  }
                  if (line.startsWith('data:')) {
                    dataLines.push(line.slice(5).trim());
                  }
                }
                if (!dataLines.length) continue;
                const maybeResult = handleMessage(eventName, dataLines.join('\n'));
                if (maybeResult) {
                  finalResult = maybeResult;
                  if (isCurrent()) {
                    setResult(maybeResult);
                    setStatus('completed');
                    abortRef.current = null;
                  }
                  resolve(maybeResult);
                  return;
                }
              }
            }

            // Flush any trailing bytes and remaining event block.
            rawBuffer += decoder.decode();
            const { blocks } = splitSseBlocks(`${rawBuffer}\n\n`);
            for (const block of blocks) {
              if (!block.trim()) continue;
              let eventName = '';
              const dataLines: string[] = [];
              for (const rawLine of block.split('\n')) {
                const line = rawLine.trimEnd();
                if (!line || line.startsWith(':')) continue;
                if (line.startsWith('event:')) eventName = line.slice(6).trim();
                if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
              }
              if (!dataLines.length) continue;
              const maybeResult = handleMessage(eventName, dataLines.join('\n'));
              if (maybeResult) finalResult = maybeResult;
            }

            if (!isCurrent()) return;
            abortRef.current = null;
            setStatus('completed');
            if (finalResult) setResult(finalResult);
            resolve(finalResult);
          })
          .catch((err: unknown) => {
            if (!isCurrent()) return;
            abortRef.current = null;
            const isAbort =
              controller.signal.aborted ||
              (err instanceof DOMException && err.name === 'AbortError') ||
              (err instanceof Error && err.name === 'AbortError');
            if (isAbort) {
              setStatus('idle');
              reject(err instanceof Error ? err : new Error('Stream aborted'));
              return;
            }
            const message = err instanceof Error ? err.message : String(err);
            setError(message);
            setStatus('error');
            reject(err instanceof Error ? err : new Error(message));
          });
      });
    },
    [reset],
  );

  return {
    state,
    progress,
    status,
    error,
    result,
    start,
    stop,
    reset,
  };
}
