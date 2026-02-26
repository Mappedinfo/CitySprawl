from __future__ import annotations

from contextlib import contextmanager
import contextvars
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging as py_logging
import os
import threading
import traceback
from typing import Any, Dict, Iterator, Mapping, Optional

from .runlog import RunLogStore


MAX_DATA_STR_LEN = 512
STACKTRACE_MAX_CHARS = 4000
_RUN_CONTEXT: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar("citygen_run_context", default=None)
_OBS_LOCK = threading.Lock()
_SETTINGS: "ObservabilitySettings | None" = None
_RUN_LOG_STORE: "RunLogStore | None" = None
_LOGGING_CONFIGURED = False


@dataclass(frozen=True)
class ObservabilitySettings:
    log_level: str = "INFO"
    log_format: str = "json"
    verbose_stream_meta: bool = False
    progress_debug: bool = False
    run_logs_enabled: bool = True
    run_log_max_runs: int = 32
    run_log_max_events_per_run: int = 5000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, min_value: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        value = int(raw)
    except Exception:
        return int(default)
    return max(int(min_value), value)


def get_observability_settings(*, refresh: bool = False) -> ObservabilitySettings:
    global _SETTINGS
    with _OBS_LOCK:
        if _SETTINGS is not None and not refresh:
            return _SETTINGS
        level = str(os.getenv("CITYGEN_LOG_LEVEL", "INFO")).strip().upper() or "INFO"
        fmt = str(os.getenv("CITYGEN_LOG_FORMAT", "json")).strip().lower() or "json"
        if fmt not in {"json", "text"}:
            fmt = "json"
        _SETTINGS = ObservabilitySettings(
            log_level=level,
            log_format=fmt,
            verbose_stream_meta=_env_bool("CITYGEN_LOG_VERBOSE_STREAM_META", False),
            progress_debug=_env_bool("CITYGEN_LOG_PROGRESS_DEBUG", False),
            run_logs_enabled=_env_bool("CITYGEN_RUN_LOGS_ENABLED", True),
            run_log_max_runs=_env_int("CITYGEN_RUN_LOG_MAX_RUNS", 32, min_value=1),
            run_log_max_events_per_run=_env_int("CITYGEN_RUN_LOG_MAX_EVENTS_PER_RUN", 5000, min_value=1),
        )
        return _SETTINGS


def get_run_log_store() -> RunLogStore:
    global _RUN_LOG_STORE
    settings = get_observability_settings()
    with _OBS_LOCK:
        if _RUN_LOG_STORE is None:
            _RUN_LOG_STORE = RunLogStore(
                enabled=settings.run_logs_enabled,
                max_runs=settings.run_log_max_runs,
                max_events_per_run=settings.run_log_max_events_per_run,
            )
        return _RUN_LOG_STORE


class CityGenJsonFormatter(py_logging.Formatter):
    def format(self, record: py_logging.LogRecord) -> str:
        payload = getattr(record, "citygen_structured", None)
        if isinstance(payload, dict):
            try:
                return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), default=str)
            except Exception:
                pass
        fallback = {
            "ts": _now_iso(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            fallback["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(fallback, ensure_ascii=True, separators=(",", ":"), default=str)


class CityGenTextFormatter(py_logging.Formatter):
    def format(self, record: py_logging.LogRecord) -> str:
        payload = getattr(record, "citygen_structured", None)
        if isinstance(payload, dict):
            ts = str(payload.get("ts", _now_iso()))
            level = str(payload.get("level", record.levelname))
            logger = str(payload.get("logger", record.name))
            event = str(payload.get("event", ""))
            run_id = str(payload.get("run_id", "")) if payload.get("run_id") is not None else ""
            msg = str(payload.get("message", record.getMessage()))
            suffix = f" event={event}" if event else ""
            if run_id:
                suffix += f" run_id={run_id}"
            return f"{ts} {level} {logger}: {msg}{suffix}"
        return super().format(record)


def configure_citygen_logging() -> None:
    global _LOGGING_CONFIGURED
    settings = get_observability_settings()
    with _OBS_LOCK:
        if _LOGGING_CONFIGURED:
            return
        logger = py_logging.getLogger("citygen")
        logger.setLevel(getattr(py_logging, settings.log_level, py_logging.INFO))
        logger.propagate = False
        handler = py_logging.StreamHandler()
        if settings.log_format == "text":
            handler.setFormatter(CityGenTextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        else:
            handler.setFormatter(CityGenJsonFormatter())
        handler.setLevel(getattr(py_logging, settings.log_level, py_logging.INFO))
        logger.handlers = [handler]
        _LOGGING_CONFIGURED = True


@contextmanager
def run_context(run_id: str, *, job_id: Optional[str] = None) -> Iterator[None]:
    token = _RUN_CONTEXT.set({
        "run_id": str(run_id),
        **({"job_id": str(job_id)} if job_id else {}),
    })
    try:
        store = get_run_log_store()
        if store.enabled:
            store.start_run(str(run_id), job_id=str(job_id) if job_id else None)
        yield
    finally:
        try:
            store = get_run_log_store()
            if store.enabled:
                store.end_run(str(run_id))
        except Exception:
            pass
        _RUN_CONTEXT.reset(token)


def _current_run_context() -> dict[str, str] | None:
    return _RUN_CONTEXT.get()


def config_hash(value: Any) -> str:
    try:
        if hasattr(value, "model_dump"):
            payload = value.model_dump()
        elif hasattr(value, "dict"):
            payload = value.dict()  # type: ignore[call-arg]
        else:
            payload = value
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    except Exception:
        encoded = repr(value).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _truncate_str(text: str, max_len: int = MAX_DATA_STR_LEN) -> str:
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}...(truncated:{len(text)-max_len})"


def _sanitize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_str(value)
    if isinstance(value, (list, tuple)):
        out = []
        for item in value[:50]:
            out.append(_sanitize_value(item))
        if len(value) > 50:
            out.append(f"...({len(value)-50} more)")
        return out
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for idx, (k, v) in enumerate(value.items()):
            if idx >= 50:
                out["__truncated__"] = f"{len(value)-50} more keys"
                break
            out[str(k)] = _sanitize_value(v)
        return out
    try:
        return _truncate_str(str(value))
    except Exception:
        return "<unserializable>"


def _safe_payload_bytes_est(payload: Any) -> int:
    try:
        return int(len(json.dumps(payload, ensure_ascii=True, default=str)))
    except Exception:
        try:
            return int(len(repr(payload)))
        except Exception:
            return 0


def summarize_stream_event_meta(event: Mapping[str, Any]) -> Dict[str, Any]:
    event_type = str(event.get("event_type", "unknown"))
    data = event.get("data", {})
    meta: Dict[str, Any] = {
        "event_type": event_type,
        "payload_bytes_est": _safe_payload_bytes_est(event),
    }
    phase = None
    if isinstance(data, Mapping):
        if event_type == "road_trace_progress":
            points = data.get("points") if isinstance(data.get("points"), list) else []
            meta.update({
                "trace_id": str(data.get("trace_id", "")),
                "road_class": str(data.get("road_class", "")),
                "complete": bool(data.get("complete", False)),
                "culdesac": bool(data.get("culdesac", False)),
                "point_count": len(points),
            })
        elif event_type == "road_polyline_added":
            pts = data.get("path_points") if isinstance(data.get("path_points"), list) else []
            meta.update({
                "edge_id": str(data.get("id", "")),
                "road_class": str(data.get("road_class", "")),
                "u": str(data.get("u", "")),
                "v": str(data.get("v", "")),
                "point_count": len(pts),
            })
        elif event_type == "road_edge_added":
            meta.update({
                "edge_id": str(data.get("id", "")),
                "road_class": str(data.get("road_class", "")),
                "u": str(data.get("u", "")),
                "v": str(data.get("v", "")),
            })
        elif event_type == "road_node_added":
            meta.update({
                "node_id": str(data.get("id", "")),
                "kind": str(data.get("kind", "")),
            })
        elif event_type == "progress":
            phase = str(data.get("phase", "")) or None
            meta.update({
                "phase": str(data.get("phase", "")),
                "progress": float(data.get("progress", 0.0) or 0.0),
                "message": _truncate_str(str(data.get("message", ""))),
            })
        elif event_type in {"road_phase_start", "road_phase_complete"}:
            phase = str(data.get("phase", "")) or None
            meta.update({"phase": str(data.get("phase", ""))})
        elif event_type == "terrain_milestone":
            phase = str(data.get("stage", "")) or None
            meta.update({
                "stage": str(data.get("stage", "")),
                "resolution": data.get("resolution"),
                "extent_m": data.get("extent_m"),
            })
        else:
            meta["payload_keys"] = [str(k) for k in list(data.keys())[:40]]
    else:
        meta["payload_type"] = type(data).__name__
    if phase is not None:
        meta["phase"] = phase
    return meta


def log_structured(
    logger: py_logging.Logger,
    level: int,
    *,
    event: str,
    message: str,
    kind: str,
    component: str,
    run_id: Optional[str] = None,
    job_id: Optional[str] = None,
    phase: Optional[str] = None,
    duration_ms: Optional[float] = None,
    data: Optional[Mapping[str, Any]] = None,
    exc: Optional[BaseException] = None,
) -> Dict[str, Any]:
    ctx = _current_run_context() or {}
    rid = str(run_id or ctx.get("run_id") or "") or None
    jid = str(job_id or ctx.get("job_id") or "") or None
    record: Dict[str, Any] = {
        "ts": _now_iso(),
        "level": py_logging.getLevelName(level),
        "logger": logger.name,
        "event": str(event),
        "message": _truncate_str(str(message), max_len=1024),
        "kind": str(kind),
        "component": str(component),
    }
    if rid is not None:
        record["run_id"] = rid
    if jid is not None:
        record["job_id"] = jid
    if phase:
        record["phase"] = str(phase)
    if duration_ms is not None:
        try:
            record["duration_ms"] = float(duration_ms)
        except Exception:
            pass
    if data:
        record["data"] = _sanitize_value(dict(data))
    else:
        record["data"] = {}
    if exc is not None:
        stack = "".join(traceback.format_exception(exc.__class__, exc, exc.__traceback__))
        record["data"]["error_type"] = exc.__class__.__name__
        record["data"]["error"] = _truncate_str(str(exc), max_len=1024)
        record["data"]["stacktrace"] = _truncate_str(stack, max_len=STACKTRACE_MAX_CHARS)
    record["data"]["thread_name"] = threading.current_thread().name

    try:
        store = get_run_log_store()
        if rid is not None and store.enabled:
            seq = store.append(rid, record, job_id=jid)
            record["seq"] = seq
        logger.log(level, message, extra={"citygen_structured": record})
    except Exception:
        try:
            logger.log(level, f"{message} [structured_log_fallback]")
        except Exception:
            pass
    return record
