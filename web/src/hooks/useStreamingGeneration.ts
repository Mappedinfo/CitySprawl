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
        // Move from partial to completed
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
        // Update partial trace
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
      // These don't affect incremental geometry state
      break;
  }

  return next;
}

export function useStreamingGeneration() {
  const [state, setState] = useState<IncrementalState>(createEmptyState());
  const [progress, setProgress] = useState<StreamingProgress>({ phase: '', progress: 0, message: '' });
  const [status, setStatus] = useState<StreamingStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StagedCityResponse | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const reset = useCallback(() => {
    setState(createEmptyState());
    setProgress({ phase: '', progress: 0, message: '' });
    setStatus('idle');
    setError(null);
    setResult(null);
  }, []);

  const stop = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setStatus('idle');
  }, []);

  const start = useCallback(
    (config: GenerateConfig): Promise<StagedCityResponse | null> => {
      return new Promise((resolve, reject) => {
        // Reset state
        reset();
        setStatus('connecting');

        // Build URL with config as query params (POST body not supported by EventSource)
        // We'll use POST endpoint with fetch instead
        const url = `${API_BASE}/api/v2/generate_stream`;

        // Use fetch with ReadableStream for SSE from POST
        fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
          },
          body: JSON.stringify(config),
        })
          .then(async (response) => {
            if (!response.ok) {
              throw new Error(`HTTP error: ${response.status}`);
            }

            const reader = response.body?.getReader();
            if (!reader) {
              throw new Error('No response body');
            }

            const decoder = new TextDecoder();
            let buffer = '';
            let currentEvent = '';
            let finalResult: StagedCityResponse | null = null;

            while (true) {
              const { done, value } = await reader.read();
              if (done) break;

              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split('\n');
              buffer = lines.pop() ?? '';

              for (const line of lines) {
                if (line.startsWith('event:')) {
                  currentEvent = line.slice(6).trim();
                  continue;
                }
                if (line.startsWith('data:')) {
                  const jsonStr = line.slice(5).trim();
                  if (!jsonStr) continue;

                  try {
                    const data = JSON.parse(jsonStr);

                    // Handle heartbeat - update status to streaming on first heartbeat
                    if (currentEvent === 'heartbeat' || data.status === 'connected' || data.status === 'generating') {
                      setStatus('streaming');
                      continue;
                    }

                    // Handle batch events
                    if (data.events && Array.isArray(data.events)) {
                      for (const event of data.events) {
                        if (event.event_type === 'progress') {
                          setProgress(event.data);
                        } else {
                          setState((prev) => mergeEvent(prev, event));
                        }
                      }
                    }

                    // Handle complete event (final result)
                    if (data.final_artifact || currentEvent === 'complete') {
                      finalResult = data as StagedCityResponse;
                      setResult(finalResult);
                      setStatus('completed');
                      resolve(finalResult);
                      return;
                    }

                    // Handle error event
                    if (data.error || currentEvent === 'error') {
                      const errorMsg = data.error || 'Unknown error';
                      setError(errorMsg);
                      setStatus('error');
                      reject(new Error(errorMsg));
                      return;
                    }
                  } catch {
                    // Ignore parse errors for partial data
                  }
                }
              }
            }

            // Stream ended without complete event
            setStatus('completed');
            resolve(finalResult);
          })
          .catch((err) => {
            setError(err.message);
            setStatus('error');
            reject(err);
          });
      });
    },
    [reset, result],
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
