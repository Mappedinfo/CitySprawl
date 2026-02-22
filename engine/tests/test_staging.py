from engine.generator import generate_city_staged
from engine.models import GenerateConfig


def test_generate_city_staged_returns_five_ordered_stages_with_required_layers():
    resp = generate_city_staged(GenerateConfig(grid_resolution=96))
    stage_ids = [s.stage_id for s in resp.stages]
    assert stage_ids == ['terrain', 'analysis', 'infrastructure', 'traffic', 'final_preview']

    terrain = resp.stages[0]
    assert terrain.layers.terrain_class_preview is not None
    assert terrain.layers.hillshade_preview is not None
    assert terrain.layers.contour_lines
    assert terrain.layers.river_area_polygons is not None

    analysis = resp.stages[1]
    assert analysis.layers.suitability_preview is not None
    assert analysis.layers.resource_sites

    traffic = resp.stages[3]
    assert traffic.layers.traffic_edge_flows

    final_stage = resp.stages[4]
    assert final_stage.layers.building_footprints
    assert final_stage.layers.green_zones_preview is not None
    assert final_stage.layers.land_blocks is not None
    assert final_stage.layers.parcel_lots is not None
