from engine.generator import generate_city
from engine.models import GenerateConfig
from engine.pydantic_compat import model_dump


def test_same_seed_same_structure_counts_and_values():
    cfg = GenerateConfig(seed=42, grid_resolution=96)
    a = generate_city(cfg)
    b = generate_city(cfg)

    assert len(a.hubs) == len(b.hubs)
    assert len(a.rivers) == len(b.rivers)
    assert len(a.roads.edges) == len(b.roads.edges)
    assert a.metrics.connected == b.metrics.connected

    pa = model_dump(a)
    pb = model_dump(b)
    # Remove volatile timestamps / durations before equality checks.
    pa["meta"]["generated_at_utc"] = "X"
    pb["meta"]["generated_at_utc"] = "X"
    pa["meta"]["duration_ms"] = 0.0
    pb["meta"]["duration_ms"] = 0.0
    assert pa == pb
