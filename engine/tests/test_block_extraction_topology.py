from math import hypot

from shapely.geometry import Polygon

from engine.blocks.extraction import BlockExtractionConfig, extract_macro_blocks
from engine.models import Point2D, RoadEdgeRecord, RoadNetwork, RoadNodeRecord


def _road_network_from_segments(segments: list[tuple[tuple[float, float], tuple[float, float]]]) -> RoadNetwork:
    node_ids: dict[tuple[float, float], str] = {}
    nodes: list[RoadNodeRecord] = []
    edges: list[RoadEdgeRecord] = []

    def node_id_for(x: float, y: float) -> str:
        key = (float(x), float(y))
        if key in node_ids:
            return node_ids[key]
        nid = f"n{len(node_ids)}"
        node_ids[key] = nid
        nodes.append(RoadNodeRecord(id=nid, x=float(x), y=float(y), kind="road"))
        return nid

    for idx, (a, b) in enumerate(segments):
        ax, ay = a
        bx, by = b
        u = node_id_for(ax, ay)
        v = node_id_for(bx, by)
        length = hypot(float(bx) - float(ax), float(by) - float(ay))
        edges.append(
            RoadEdgeRecord(
                id=f"e{idx}",
                u=u,
                v=v,
                road_class="collector",
                weight=length,
                length_m=length,
                width_m=18.0,
                render_order=1,
                path_points=[Point2D(x=float(ax), y=float(ay)), Point2D(x=float(bx), y=float(by))],
            )
        )
    return RoadNetwork(nodes=nodes, edges=edges)


def _oriented_metrics(poly: Polygon) -> tuple[float, float]:
    mrr = poly.minimum_rotated_rectangle
    coords = list(mrr.exterior.coords) if isinstance(mrr, Polygon) else []
    dims: list[float] = []
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


def test_extract_macro_blocks_topology_finds_bounded_faces_for_closed_grid():
    extent = 1_000.0
    xs = [0.0, 500.0, 1000.0]
    ys = [0.0, 500.0, 1000.0]
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for x in xs:
        for y0, y1 in zip(ys[:-1], ys[1:]):
            segments.append(((x, y0), (x, y1)))
    for y in ys:
        for x0, x1 in zip(xs[:-1], xs[1:]):
            segments.append(((x0, y), (x1, y)))

    net = _road_network_from_segments(segments)
    cfg = BlockExtractionConfig(
        min_block_area_m2=500.0,
        min_block_width_m=8.0,
        max_block_span_m=700.0,
        max_block_area_m2=250_000.0,
    )
    extraction = extract_macro_blocks(extent, net, [], config=cfg)

    assert extraction.topology_block_count >= 4
    interior_blocks = [b for b in extraction.macro_blocks if not b.boundary.intersects(extraction.boundary.boundary)]
    assert len(interior_blocks) >= 4
    for poly in interior_blocks:
        assert poly.is_valid
        assert not poly.is_empty
        assert len(poly.interiors) == 0
        span, aspect = _oriented_metrics(poly)
        assert span <= cfg.max_block_span_m * 1.1
        assert aspect <= cfg.max_block_aspect_ratio * 1.1


def test_extract_macro_blocks_open_parallel_lines_falls_back_to_bounded_superblocks():
    extent = 1_000.0
    segments = [
        ((250.0, 0.0), (250.0, extent)),
        ((500.0, 0.0), (500.0, extent)),
        ((750.0, 0.0), (750.0, extent)),
    ]
    net = _road_network_from_segments(segments)
    cfg = BlockExtractionConfig(
        min_block_area_m2=1_000.0,
        min_block_width_m=8.0,
        max_block_span_m=280.0,
        max_block_aspect_ratio=6.0,
        max_block_area_m2=60_000.0,
        split_max_depth=8,
    )
    extraction = extract_macro_blocks(extent, net, [], config=cfg)

    assert extraction.fallback_block_count > 0
    assert extraction.macro_blocks
    for poly in extraction.macro_blocks:
        assert poly.is_valid
        assert len(poly.interiors) == 0
        span, aspect = _oriented_metrics(poly)
        assert span <= cfg.max_block_span_m * 1.1
        assert aspect <= cfg.max_block_aspect_ratio * 1.25
