from engine.generator import generate_city_staged
from engine.models import GenerateConfig


def test_generate_city_staged_returns_twelve_ordered_stages_with_required_layers():
    resp = generate_city_staged(GenerateConfig(grid_resolution=96))
    stage_ids = [s.stage_id for s in resp.stages]
    assert stage_ids == [
        'start',
        'terrain',
        'rivers',
        'hubs',
        'roads',
        'artifact',
        'analysis',
        'traffic',
        'buildings',
        'parcels',
        'stages',
        'done',
    ]
    stage_by_id = {s.stage_id: s for s in resp.stages}

    terrain = stage_by_id['terrain']
    assert terrain.layers.terrain_class_preview is not None
    assert terrain.layers.hillshade_preview is not None
    assert terrain.layers.contour_lines
    assert terrain.layers.river_area_polygons is not None
    assert terrain.layers.visual_envelope is not None
    assert resp.final_artifact.terrain.extent_m == 10000.0
    assert 0.10 <= resp.final_artifact.metrics.river_coverage_ratio <= 0.30
    assert resp.final_artifact.visual_envelope is not None
    assert resp.final_artifact.metrics.visual_envelope_area_ratio is not None

    analysis = stage_by_id['analysis']
    assert analysis.layers.suitability_preview is not None
    assert analysis.layers.resource_sites
    assert analysis.layers.visual_envelope is not None

    traffic = stage_by_id['traffic']
    assert traffic.layers.traffic_edge_flows

    final_stage = stage_by_id['done']
    assert final_stage.layers.building_footprints
    assert final_stage.layers.green_zones_preview is not None
    assert final_stage.layers.land_blocks is not None
    assert final_stage.layers.parcel_lots is not None
    assert final_stage.layers.visual_envelope is not None
    assert any(edge.path_points for edge in resp.final_artifact.roads.edges if edge.road_class in ("arterial", "local"))
