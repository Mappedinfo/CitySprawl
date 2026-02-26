from engine.observability.runlog import RunLogStore


def test_runlog_store_ring_buffer_and_since_seq():
    store = RunLogStore(enabled=True, max_runs=4, max_events_per_run=3)
    store.start_run("r1")
    for i in range(4):
        store.append(
            "r1",
            {
                "ts": f"2026-01-01T00:00:0{i}Z",
                "level": "INFO",
                "logger": "citygen.test",
                "event": f"e{i}",
                "message": f"msg{i}",
                "kind": "lifecycle",
                "component": "test",
                "run_id": "r1",
                "data": {"i": i},
            },
        )
    out = store.get_logs("r1")
    assert out is not None
    assert out["last_seq"] == 4
    assert out["dropped_count"] == 1
    assert [rec["seq"] for rec in out["logs"]] == [2, 3, 4]

    out2 = store.get_logs("r1", since_seq=2)
    assert out2 is not None
    assert [rec["seq"] for rec in out2["logs"]] == [3, 4]


def test_runlog_store_filters_and_run_eviction():
    store = RunLogStore(enabled=True, max_runs=2, max_events_per_run=10)
    store.start_run("r1")
    store.append("r1", {"ts": "t", "level": "INFO", "logger": "x", "event": "a", "message": "m", "kind": "progress", "component": "c", "data": {}})
    store.append("r1", {"ts": "t", "level": "ERROR", "logger": "x", "event": "b", "message": "m", "kind": "error", "component": "c", "data": {}})
    only_error = store.get_logs("r1", level="ERROR")
    assert only_error is not None
    assert [rec["event"] for rec in only_error["logs"]] == ["b"]
    only_progress = store.get_logs("r1", kind="progress")
    assert only_progress is not None
    assert [rec["event"] for rec in only_progress["logs"]] == ["a"]

    store.start_run("r2")
    store.append("r2", {"ts": "t", "level": "INFO", "logger": "x", "event": "c", "message": "m", "kind": "lifecycle", "component": "c", "data": {}})
    store.start_run("r3")
    store.append("r3", {"ts": "t", "level": "INFO", "logger": "x", "event": "d", "message": "m", "kind": "lifecycle", "component": "c", "data": {}})
    assert store.get_logs("r1") is None
    assert store.get_logs("r2") is not None
    assert store.get_logs("r3") is not None
