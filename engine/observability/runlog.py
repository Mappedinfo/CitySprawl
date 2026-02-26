from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Deque, Dict, Iterable, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_level_name(level: str | None) -> str | None:
    if level is None:
        return None
    name = str(level).strip().upper()
    if not name:
        return None
    if name == "WARNING":
        return "WARN"
    return name


@dataclass
class _RunBuffer:
    run_id: str
    job_id: Optional[str] = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    last_seq: int = 0
    dropped_count: int = 0
    records: Deque[Dict[str, Any]] = field(default_factory=deque)


class RunLogStore:
    """Thread-safe in-memory ring buffer for structured run logs."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_runs: int = 32,
        max_events_per_run: int = 5000,
    ) -> None:
        self.enabled = bool(enabled)
        self.max_runs = max(1, int(max_runs))
        self.max_events_per_run = max(1, int(max_events_per_run))
        self._lock = RLock()
        self._runs: "OrderedDict[str, _RunBuffer]" = OrderedDict()

    def _ensure_run_locked(self, run_id: str, *, job_id: Optional[str] = None) -> _RunBuffer:
        key = str(run_id)
        buf = self._runs.get(key)
        if buf is None:
            while len(self._runs) >= self.max_runs:
                self._runs.popitem(last=False)
            buf = _RunBuffer(run_id=key, job_id=(str(job_id) if job_id else None))
            self._runs[key] = buf
        else:
            if job_id and not buf.job_id:
                buf.job_id = str(job_id)
            self._runs.move_to_end(key)
        return buf

    def start_run(self, run_id: str, *, job_id: Optional[str] = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            buf = self._ensure_run_locked(str(run_id), job_id=job_id)
            buf.updated_at = _now_iso()

    def end_run(self, run_id: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            buf = self._runs.get(str(run_id))
            if buf is None:
                return
            buf.updated_at = _now_iso()
            self._runs.move_to_end(str(run_id))

    def append(self, run_id: str, record: Dict[str, Any], *, job_id: Optional[str] = None) -> int:
        if not self.enabled:
            return int(record.get("seq", 0) or 0)
        with self._lock:
            buf = self._ensure_run_locked(str(run_id), job_id=job_id)
            buf.last_seq += 1
            seq = int(buf.last_seq)
            entry = dict(record)
            entry["seq"] = seq
            if "run_id" not in entry:
                entry["run_id"] = buf.run_id
            if buf.job_id and "job_id" not in entry:
                entry["job_id"] = buf.job_id
            if "ts" not in entry:
                entry["ts"] = _now_iso()
            if len(buf.records) >= self.max_events_per_run:
                buf.records.popleft()
                buf.dropped_count += 1
            buf.records.append(entry)
            buf.updated_at = str(entry.get("ts") or _now_iso())
            self._runs.move_to_end(str(run_id))
            return seq

    def get_logs(
        self,
        run_id: str,
        *,
        since_seq: int = 0,
        level: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 500,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        level_name = _normalize_level_name(level)
        kind_name = str(kind).strip() if kind is not None else None
        max_limit = max(1, min(int(limit), 2000))
        with self._lock:
            buf = self._runs.get(str(run_id))
            if buf is None:
                return None
            self._runs.move_to_end(str(run_id))
            records: list[Dict[str, Any]] = []
            for rec in buf.records:
                seq = int(rec.get("seq", 0) or 0)
                if seq <= int(since_seq):
                    continue
                if level_name is not None and _normalize_level_name(str(rec.get("level", ""))) != level_name:
                    continue
                if kind_name is not None and str(rec.get("kind", "")) != kind_name:
                    continue
                records.append(dict(rec))
                if len(records) >= max_limit:
                    break
            return {
                "run_id": buf.run_id,
                "job_id": buf.job_id,
                "created_at": buf.created_at,
                "updated_at": buf.updated_at,
                "last_seq": int(buf.last_seq),
                "dropped_count": int(buf.dropped_count),
                "logs": records,
            }

    def iter_run_ids(self) -> Iterable[str]:
        if not self.enabled:
            return []
        with self._lock:
            return list(self._runs.keys())
