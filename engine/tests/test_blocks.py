from math import hypot

from shapely.geometry import Polygon

from engine.generator import generate_city_staged
from engine.models import GenerateConfig


ALLOWED_PARCEL_CLASSES = {
    "residential_candidate",
    "commercial_candidate",
    "industrial_candidate",
    "green_candidate",
    "public_facility_candidate",
}


def _bbox_span_and_aspect(points) -> tuple[float, float]:
    if not points or len(points) < 3:
        return 0.0, 1.0
    poly = Polygon([(float(p.x), float(p.y)) for p in points])
    if poly.is_empty:
        return 0.0, 1.0
    try:
        mrr = poly.minimum_rotated_rectangle
        coords = list(mrr.exterior.coords) if isinstance(mrr, Polygon) else []
    except Exception:
        coords = []
    dims = []
    if len(coords) >= 5:
        for i in range(4):
            x0, y0 = coords[i]
            x1, y1 = coords[i + 1]
            d = hypot(x1 - x0, y1 - y0)
            if d > 1e-9:
                dims.append(float(d))
    if len(dims) < 2:
        minx, miny, maxx, maxy = poly.bounds
        dims = [float(maxx - minx), float(maxy - miny)]
    span = max(dims) if dims else 0.0
    nonzero = [d for d in dims if d > 1e-9]
    aspect = span / max(min(nonzero) if nonzero else 1.0, 1e-9)
    return float(span), float(aspect)


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
    assert len(final_stage.layers.land_blocks) == len(artifact.blocks or [])
    assert len(final_stage.layers.parcel_lots) == len(artifact.parcels or [])

    for edge in artifact.roads.edges:
        assert edge.width_m > 0.0
        assert edge.render_order in (0, 1, 2)

    for parcel in artifact.parcels[:200]:
        assert parcel.area_m2 > 0.0
        assert parcel.parcel_class in ALLOWED_PARCEL_CLASSES

    block_ids = {b.id for b in (artifact.blocks or [])}
    assert all((p.parent_block_id in block_ids) for p in (artifact.parcels or []))

    extent = float(artifact.terrain.extent_m)
    assert not any(
        _bbox_span_and_aspect(block.points)[0] > 0.35 * extent
        for block in (artifact.blocks or [])
    )
    assert not any(
        _bbox_span_and_aspect(block.points)[1] > 20.0
        for block in (artifact.blocks or [])
    )
    elongated_large_parcels = sum(
        1
        for parcel in (artifact.parcels or [])
        if parcel.area_m2 > 1000.0 and _bbox_span_and_aspect(parcel.points)[1] > 35.0
    )
    # Coverage-first local infill can create a few long, narrow residual lots in
    # edge cases; guard against broad regressions while tolerating rare outliers.
    assert elongated_large_parcels <= max(6, int(0.001 * len(artifact.parcels or [])))
