import { useEffect, useMemo, useRef, useState } from 'react';

import type { StageArtifact } from '../types/city';

export type TimelineController = {
  totalMs: number;
  currentTimeMs: number;
  currentStageIndex: number;
  playing: boolean;
  setPlaying: (next: boolean) => void;
  togglePlaying: () => void;
  seek: (timeMs: number) => void;
  selectStage: (index: number) => void;
  reset: (autoPlay?: boolean) => void;
};

function clamp(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v));
}

export function useTimelinePlayer(stages: StageArtifact[], totalMs = 20_000): TimelineController {
  const [currentTimeMs, setCurrentTimeMs] = useState(0);
  const [playing, setPlaying] = useState(true);
  const rafRef = useRef<number | null>(null);
  const lastRef = useRef<number | null>(null);

  const stageStarts = useMemo(() => stages.map((s) => s.timestamp_ms).sort((a, b) => a - b), [stages]);

  const currentStageIndex = useMemo(() => {
    if (!stageStarts.length) return 0;
    let idx = 0;
    for (let i = 0; i < stageStarts.length; i += 1) {
      if (currentTimeMs >= stageStarts[i]) idx = i;
      else break;
    }
    return idx;
  }, [currentTimeMs, stageStarts]);

  useEffect(() => {
    if (!playing) {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      lastRef.current = null;
      return;
    }

    const tick = (ts: number) => {
      if (lastRef.current == null) lastRef.current = ts;
      const dt = ts - lastRef.current;
      lastRef.current = ts;
      setCurrentTimeMs((prev) => {
        const next = prev + dt;
        if (next >= totalMs) {
          setPlaying(false);
          return totalMs;
        }
        return next;
      });
      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      lastRef.current = null;
    };
  }, [playing, totalMs]);

  useEffect(() => {
    setCurrentTimeMs((prev) => clamp(prev, 0, totalMs));
  }, [totalMs]);

  const seek = (timeMs: number) => setCurrentTimeMs(clamp(timeMs, 0, totalMs));
  const selectStage = (index: number) => {
    if (!stages.length) {
      seek(0);
      return;
    }
    const safe = clamp(index, 0, stages.length - 1);
    seek(stages[safe]?.timestamp_ms ?? 0);
    setPlaying(false);
  };
  const togglePlaying = () => setPlaying((prev) => !prev);
  const reset = (autoPlay = true) => {
    setCurrentTimeMs(0);
    setPlaying(autoPlay);
    lastRef.current = null;
  };

  return {
    totalMs,
    currentTimeMs,
    currentStageIndex,
    playing,
    setPlaying,
    togglePlaying,
    seek,
    selectStage,
    reset,
  };
}
