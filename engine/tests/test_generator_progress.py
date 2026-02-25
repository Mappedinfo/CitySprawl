from engine.generator import generate_city_staged
from engine.models import GenerateConfig


CANONICAL_PHASES = [
    "start",
    "terrain",
    "rivers",
    "hubs",
    "roads",
    "artifact",
    "analysis",
    "traffic",
    "buildings",
    "parcels",
    "stages",
    "done",
]


def test_generate_city_staged_emits_canonical_progress_phases():
    seen: list[str] = []

    def progress_cb(phase: str, progress: float, message: str) -> None:
        assert 0.0 <= float(progress) <= 1.0
        assert isinstance(message, str) and message
        seen.append(str(phase))

    generate_city_staged(
        GenerateConfig(
            seed=31,
            grid_resolution=96,
            hubs={"t1_count": 1, "t2_count": 2, "t3_count": 8, "min_distance_m": 80.0},
            roads={"k_neighbors": 4, "loop_budget": 2, "branch_steps": 1, "slope_penalty": 2.0, "river_cross_penalty": 220.0},
        ),
        progress_cb=progress_cb,
    )

    assert seen, "expected progress callbacks"
    assert "traffic" in seen

    forbidden = {
        "terrain_visuals",
        "naming",
        "core_complete",
        "analysis_complete",
    }
    assert not (forbidden & set(seen))
    assert not any(phase.startswith("roads.") for phase in seen)

    first_index = {phase: seen.index(phase) for phase in CANONICAL_PHASES}
    assert sorted(first_index, key=first_index.get) == CANONICAL_PHASES
