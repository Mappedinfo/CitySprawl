from shapely.geometry import Polygon

from engine.blocks.parcelize import FrontageParcelConfig, generate_frontage_parcels
from engine.models import Point2D, RoadEdgeRecord, RoadNetwork, RoadNodeRecord


def _road_network_with_local_cul() -> RoadNetwork:
    nodes = [
        RoadNodeRecord(id="n0", x=0.0, y=0.0, kind="arterial"),
        RoadNodeRecord(id="n1", x=200.0, y=0.0, kind="arterial"),
        RoadNodeRecord(id="n2", x=100.0, y=0.0, kind="major_local"),
        RoadNodeRecord(id="n3", x=100.0, y=80.0, kind="major_local"),
        RoadNodeRecord(id="n4", x=120.0, y=120.0, kind="minor_local"),
        RoadNodeRecord(id="n5", x=150.0, y=145.0, kind="minor_local"),
    ]
    edges = [
        RoadEdgeRecord(
            id="art0",
            u="n0",
            v="n1",
            road_class="arterial",
            weight=1.0,
            length_m=200.0,
            river_crossings=0,
            width_m=18.0,
            render_order=0,
            path_points=[Point2D(x=0.0, y=0.0), Point2D(x=200.0, y=0.0)],
        ),
        RoadEdgeRecord(
            id="col0",
            u="n2",
            v="n3",
            road_class="major_local",
            weight=1.0,
            length_m=80.0,
            river_crossings=0,
            width_m=11.0,
            render_order=1,
            path_points=[Point2D(x=100.0, y=0.0), Point2D(x=100.0, y=80.0)],
        ),
        RoadEdgeRecord(
            id="loc-cul",
            u="n4",
            v="n5",
            road_class="minor_local",
            weight=1.0,
            length_m=45.0,
            river_crossings=0,
            width_m=6.0,
            render_order=2,
            path_points=[Point2D(x=120.0, y=120.0), Point2D(x=135.0, y=132.0), Point2D(x=150.0, y=145.0)],
        ),
    ]
    return RoadNetwork(nodes=nodes, edges=edges)


def test_frontage_parcels_accept_local_morphology_coupling_config_and_emit_valid_polys():
    blocks = [Polygon([(20.0, 20.0), (220.0, 20.0), (220.0, 220.0), (20.0, 220.0)])]
    road_network = _road_network_with_local_cul()
    result = generate_frontage_parcels(
        blocks,
        road_network=road_network,
        river_areas=[],
        config=FrontageParcelConfig(
            residential_target_area_m2=1600.0,
            mixed_target_area_m2=2400.0,
            min_frontage_m=10.0,
            min_depth_m=12.0,
            parcel_local_morphology_coupling=True,
            parcel_culdesac_frontage_relaxation=0.25,
            parcel_local_depth_bias=0.2,
            parcel_curvilinear_split_bias=0.35,
            seed=3,
        ),
    )
    assert result.parcel_polygons_by_block
    parcels = result.parcel_polygons_by_block[0]
    assert len(parcels) > 0
    assert all(float(p.area) > 0.0 for p in parcels)
    assert all(bool(p.is_valid) for p in parcels)

