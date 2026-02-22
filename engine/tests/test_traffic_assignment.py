from engine.generator import generate_city
from engine.models import GenerateConfig
from engine.traffic import assign_edge_flows


def test_assign_edge_flows_returns_nonempty_and_finite():
    art = generate_city(GenerateConfig(grid_resolution=96))
    result = assign_edge_flows(art.hubs, art.roads)
    assert len(result.edge_flows) == len(art.roads.edges)
    assert result.od_pair_count > 0
    assert result.max_flow >= 0
    assert result.max_congestion_ratio >= 0
    assert any(flow.flow > 0 for flow in result.edge_flows)
    assert all(flow.capacity > 0 for flow in result.edge_flows)
