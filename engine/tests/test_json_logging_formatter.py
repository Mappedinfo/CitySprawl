import json
import logging

from engine.observability.logging import CityGenJsonFormatter, log_structured, summarize_stream_event_meta


def test_json_formatter_emits_structured_payload():
    record = logging.LogRecord("citygen.test", logging.INFO, __file__, 10, "ignored", (), None)
    record.citygen_structured = {
        "ts": "2026-01-01T00:00:00Z",
        "level": "INFO",
        "logger": "citygen.test",
        "event": "evt",
        "message": "hello",
        "kind": "lifecycle",
        "component": "test",
        "run_id": "r1",
        "data": {"k": "v"},
    }
    line = CityGenJsonFormatter().format(record)
    body = json.loads(line)
    assert body["event"] == "evt"
    assert body["run_id"] == "r1"
    assert body["data"]["k"] == "v"


def test_log_structured_sanitizes_unserializable_and_long_strings():
    logger = logging.getLogger("citygen.test.formatter")
    long_text = "x" * 800
    rec = log_structured(
        logger,
        logging.INFO,
        event="sanitize",
        message="sanitize test",
        kind="lifecycle",
        component="test",
        data={"obj": object(), "text": long_text},
    )
    assert "data" in rec
    assert "obj" in rec["data"]
    assert isinstance(rec["data"]["obj"], str)
    assert "truncated" in rec["data"]["text"]


def test_summarize_stream_event_meta_omits_geometry_arrays():
    event = {
        "event_type": "road_polyline_added",
        "data": {
            "id": "edge-1",
            "u": "a",
            "v": "b",
            "road_class": "arterial",
            "path_points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}],
        },
    }
    meta = summarize_stream_event_meta(event)
    assert meta["event_type"] == "road_polyline_added"
    assert meta["point_count"] == 2
    assert "path_points" not in meta
