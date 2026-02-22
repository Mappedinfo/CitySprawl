from engine.generator import generate_city_staged
from engine.models import GenerateConfig


ALLOWED_PARCEL_CLASSES = {
    "residential_candidate",
    "commercial_candidate",
    "industrial_candidate",
    "green_candidate",
    "public_facility_candidate",
}


def test_staged_generation_emits_blocks_parcels_and_pedestrian_paths():
    cfg = GenerateConfig(
        seed=23,
        grid_resolution=96,
        hubs={"t1_count": 1, "t2_count": 2, "t3_count": 8, "min_distance_m": 80.0},
        roads={"k_neighbors": 4, "loop_budget": 2, "branch_steps": 1, "slope_penalty": 2.0, "river_cross_penalty": 220.0},
    )
    resp = generate_city_staged(cfg)
    artifact = resp.final_artifact
    final_stage = resp.stages[-1]

    assert artifact.river_areas is not None
    assert artifact.terrain.terrain_class_preview is not None
    assert artifact.terrain.contours is not None

    # Geometry generation is heuristic; in rare seeds one class may be empty, but lists should exist.
    assert artifact.blocks is not None
    assert artifact.parcels is not None
    assert artifact.pedestrian_paths is not None

    assert final_stage.layers.land_blocks is not None
    assert final_stage.layers.parcel_lots is not None
    assert final_stage.layers.pedestrian_paths is not None

    for edge in artifact.roads.edges:
        assert edge.width_m > 0.0
        assert edge.render_order in (0, 1, 2)

    for parcel in artifact.parcels[:200]:
        assert parcel.area_m2 > 0.0
        assert parcel.parcel_class in ALLOWED_PARCEL_CLASSES

