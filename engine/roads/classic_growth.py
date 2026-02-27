from __future__ import annotations

from dataclasses import dataclass, field
import heapq
from math import hypot
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from engine.core.geometry import Segment, Vec2, point_segment_distance, project_point_to_segment, segment_intersection
from engine.roads.terrain_probe import TerrainProbe, TerrainProbeConfig


StreamCallback = Callable[[Dict[str, Any]], None]


def _emit_stream_event(stream_cb: Optional[StreamCallback], event: Dict[str, Any]) -> None:
    """Emit a streaming event if callback is provided."""
    if stream_cb is None:
        return
    try:
        stream_cb(event)
    except Exception:
        return


@dataclass
class ClassicMajorLocalConfig:
    classic_probe_step_m: float = 24.0
    classic_seed_spacing_m: float = 260.0
    classic_max_trace_len_m: float = 1800.0
    classic_min_trace_len_m: float = 1000.0
    classic_turn_limit_deg: float = 38.0
    classic_branch_prob: float = 0.35
    classic_continue_prob: float = 0.80
    classic_culdesac_prob: float = 0.18
    classic_max_queue_size: int = 2000
    classic_max_segments: int = 1200
    classic_max_arterial_distance_m: float = 800.0
    classic_depth_decay_power: float = 1.5
    slope_straight_threshold_deg: float = 5.0
    slope_serpentine_threshold_deg: float = 15.0
    slope_hard_limit_deg: float = 22.0
    contour_follow_weight: float = 0.9
    arterial_align_weight: float = 0.6
    hub_seek_weight: float = 0.25
    river_snap_dist_m: float = 28.0
    river_parallel_bias_weight: float = 1.0
    river_avoid_weight: float = 1.2
    river_setback_m: float = 18.0


@dataclass(order=True)
class _QueueState:
    priority: float
    pos: Vec2 = field(compare=False)
    direction: Vec2 = field(compare=False)
    depth: int = field(compare=False, default=0)
    seed_kind: str = field(compare=False, default="arterial_portal")
    must_attach_arterial: bool = field(compare=False, default=False)
    arterial_attach_budget_steps: int = field(compare=False, default=0)
    riverfront_bias_steps_remaining: int = field(compare=False, default=0)
    arterial_attached: bool = field(compare=False, default=False)


@dataclass
class _Trace:
    points: list[Vec2]
    connection_count: int
    culdesac: bool
    reason: str
    seed_kind: str = "arterial_portal"
    arterial_t_attached: bool = False
    network_attach_fallback: bool = False
    failed_arterial_attach: bool = False


@dataclass(frozen=True)
class ClassicSeed:
    pos: Vec2
    seed_kind: str
    must_attach_arterial: bool = False
    riverfront_bias_steps: int = 0


def _polyline_length(points: Sequence[Vec2]) -> float:
    return float(sum(points[i].distance_to(points[i + 1]) for i in range(len(points) - 1)))


def _iter_polyline_points(edge: object, node_lookup: dict[str, Vec2]) -> list[Vec2]:
    path = getattr(edge, "path_points", None)
    if path and len(path) >= 2:
        out: list[Vec2] = []
        for p in path:
            x = p.x if hasattr(p, "x") else p["x"]
            y = p.y if hasattr(p, "y") else p["y"]
            out.append(Vec2(float(x), float(y)))
        if len(out) >= 2:
            return out
    u = node_lookup.get(str(getattr(edge, "u", "")))
    v = node_lookup.get(str(getattr(edge, "v", "")))
    if u is None or v is None:
        return []
    return [u, v]


def _polyline_segments(points: Sequence[Vec2]) -> list[Segment]:
    return [Segment(points[i], points[i + 1]) for i in range(len(points) - 1) if points[i].distance_to(points[i + 1]) > 1e-6]


def _flatten_segments_from_edges(
    edges: Sequence[object],
    nodes: Sequence[object],
    *,
    road_classes: Optional[set[str]] = None,
) -> list[Segment]:
    node_lookup = {
        str(getattr(n, "id")): Vec2(float(getattr(n, "pos").x), float(getattr(n, "pos").y))
        for n in nodes
        if hasattr(n, "pos")
    }
    out: list[Segment] = []
    for e in edges:
        rc = str(getattr(e, "road_class", ""))
        if road_classes is not None and rc not in road_classes:
            continue
        pts = _iter_polyline_points(e, node_lookup)
        out.extend(_polyline_segments(pts))
    return out


def _nearest_road_distance_and_projection(p: Vec2, segments: Sequence[Segment]) -> tuple[float, Optional[Vec2]]:
    best_d = float("inf")
    best_proj = None
    for seg in segments:
        proj = project_point_to_segment(p, seg)
        d = p.distance_to(proj)
        if d < best_d:
            best_d = d
            best_proj = proj
    return float(best_d), best_proj


def _nearest_segment_tangent(p: Vec2, segments: Sequence[Segment]) -> tuple[Optional[Vec2], float]:
    best_tan: Optional[Vec2] = None
    best_d = float("inf")
    for seg in segments:
        d = point_segment_distance(p, seg)
        if d < best_d:
            best_d = d
            v = seg.vector().normalized()
            if v.length() > 1e-9:
                best_tan = v
    return best_tan, float(best_d)


def _nearest_hub_vector(p: Vec2, hubs: Sequence[object]) -> Optional[Vec2]:
    best = None
    best_d = float("inf")
    for h in hubs:
        pos = getattr(h, "pos", None)
        if pos is None:
            x = getattr(h, "x", None)
            y = getattr(h, "y", None)
            if x is None or y is None:
                continue
            hp = Vec2(float(x), float(y))
        else:
            hp = Vec2(float(pos.x), float(pos.y))
        d = hp.distance_to(p)
        if d < best_d:
            best_d = d
            best = (hp - p).normalized()
    return best


def _turn_vec(d: Vec2, angle_deg: float) -> Vec2:
    a = np.deg2rad(float(angle_deg))
    ca = float(np.cos(a))
    sa = float(np.sin(a))
    return Vec2(d.x * ca - d.y * sa, d.x * sa + d.y * ca).normalized()


def _angle_between_deg(a: Vec2, b: Vec2) -> float:
    if a.length() <= 1e-9 or b.length() <= 1e-9:
        return 0.0
    dot = max(-1.0, min(1.0, a.normalized().dot(b.normalized())))
    return float(np.degrees(np.arccos(dot)))


def _clamp_turn(prev_dir: Vec2, new_dir: Vec2, max_turn_deg: float) -> Vec2:
    if prev_dir.length() <= 1e-9 or new_dir.length() <= 1e-9:
        return new_dir.normalized()
    d = new_dir.normalized()
    p = prev_dir.normalized()
    if d.dot(p) < 0.0:
        d = Vec2(-d.x, -d.y)
    ang = _angle_between_deg(p, d)
    if ang <= max_turn_deg:
        return d
    w = max(0.0, min(1.0, float(max_turn_deg) / max(ang, 1e-6)))
    m = Vec2(p.x * (1.0 - w) + d.x * w, p.y * (1.0 - w) + d.y * w)
    return m.normalized() if m.length() > 1e-9 else p


def _build_forbidden_geom(river_union: object, setback_m: float) -> object:
    if river_union is None or getattr(river_union, "is_empty", True):
        return None
    try:
        return river_union.buffer(float(max(0.0, setback_m)))
    except Exception:
        return None


def _point_in_forbidden_geom(p: Vec2, geom: object) -> bool:
    if geom is None or getattr(geom, "is_empty", True):
        return False
    try:
        from shapely.geometry import Point  # type: ignore
    except Exception:
        return False
    try:
        return bool(geom.contains(Point(float(p.x), float(p.y))))
    except Exception:
        return False


def _shapely_point(p: Vec2):
    from shapely.geometry import Point  # type: ignore

    return Point(float(p.x), float(p.y))


def seed_classic_portals(
    *,
    extent_m: float,
    nodes: Sequence[object],
    edges: Sequence[object],
    blocks: Optional[Sequence[object]],
    river_union: object,
    cfg: ClassicMajorLocalConfig,
    seed: int,
) -> list[ClassicSeed]:
    rng = np.random.default_rng(int(seed) + 8301)
    node_lookup = {
        str(getattr(n, "id")): Vec2(float(getattr(n, "pos").x), float(getattr(n, "pos").y))
        for n in nodes
        if hasattr(n, "pos")
    }
    seeds: list[ClassicSeed] = []

    def add_seed(
        p: Vec2,
        *,
        seed_kind: str,
        must_attach_arterial: bool = False,
        riverfront_bias_steps: int = 0,
    ) -> None:
        if not (0.0 <= p.x <= extent_m and 0.0 <= p.y <= extent_m):
            return
        if any(p.distance_to(s.pos) < 0.6 * float(cfg.classic_seed_spacing_m) for s in seeds):
            return
        seeds.append(
            ClassicSeed(
                pos=p,
                seed_kind=str(seed_kind),
                must_attach_arterial=bool(must_attach_arterial),
                riverfront_bias_steps=int(max(0, riverfront_bias_steps)),
            )
        )

    spacing = max(20.0, float(cfg.classic_seed_spacing_m))
    riverfront_bias_steps_default = max(4, int(round(max(6.0, float(cfg.river_snap_dist_m)) / max(8.0, float(cfg.classic_probe_step_m)))))
    for edge in edges:
        if str(getattr(edge, "road_class", "")) != "arterial":
            continue
        pts = _iter_polyline_points(edge, node_lookup)
        segs = _polyline_segments(pts)
        if not segs:
            continue
        total = 0.0
        cum = [0.0]
        for seg in segs:
            total += seg.length()
            cum.append(total)
        if total < 40.0:
            continue
        n = max(1, int(total / spacing))
        skip_mid = bool(int(getattr(edge, "river_crossings", 0)) > 0)
        for i in range(n):
            d = (i + 1) / float(n + 1) * total
            if skip_mid and abs(d - 0.5 * total) < 0.18 * total:
                continue
            for si, seg in enumerate(segs):
                if cum[si] <= d <= cum[si + 1]:
                    t = (d - cum[si]) / max(cum[si + 1] - cum[si], 1e-9)
                    p = seg.point_at(t)
                    add_seed(p, seed_kind="arterial_portal")
                    if river_union is not None and not getattr(river_union, "is_empty", True):
                        try:
                            dist_r = float(river_union.distance(_shapely_point(p)))
                        except Exception:
                            dist_r = float("inf")
                        if dist_r <= max(float(cfg.river_snap_dist_m) * 4.0, float(cfg.river_setback_m) + 12.0):
                            try:
                                from shapely.ops import nearest_points  # type: ignore
                            except Exception:
                                pass
                            else:
                                try:
                                    rp, _ = nearest_points(river_union, _shapely_point(p))
                                    dx = float(p.x - rp.x)
                                    dy = float(p.y - rp.y)
                                    mag = hypot(dx, dy)
                                    if mag > 1e-6:
                                        offset = float(cfg.river_setback_m) + min(0.25 * spacing, 90.0)
                                        rp_seed = Vec2(float(rp.x + dx / mag * offset), float(rp.y + dy / mag * offset))
                                        add_seed(
                                            rp_seed,
                                            seed_kind="riverfront_arterial",
                                            must_attach_arterial=True,
                                            riverfront_bias_steps=riverfront_bias_steps_default + 2,
                                        )
                                except Exception:
                                    pass
                    break

    for block in blocks or []:
        area = float(getattr(block, "area", 0.0) or 0.0)
        if area < max(spacing * spacing * 0.8, 30_000.0):
            continue
        c = getattr(block, "centroid", None)
        if c is not None:
            add_seed(Vec2(float(c.x), float(c.y)), seed_kind="block_centroid")
        if area > max(spacing * spacing * 2.2, 120_000.0):
            rep = getattr(block, "representative_point", None)
            if callable(rep):
                p = rep()
                add_seed(Vec2(float(p.x), float(p.y)), seed_kind="block_centroid")

    # River-adjacent offset seeds to encourage waterfront major_local roads.
    if river_union is not None and not getattr(river_union, "is_empty", True):
        try:
            from shapely.geometry import Point  # type: ignore
            from shapely.ops import nearest_points  # type: ignore
        except Exception:
            return seeds
        for block in blocks or []:
            c = getattr(block, "centroid", None)
            if c is None:
                continue
            cp = Point(float(c.x), float(c.y))
            dist = float(river_union.distance(cp))
            if not (float(cfg.river_setback_m) + 5.0 <= dist <= float(cfg.river_snap_dist_m) * 6.0):
                continue
            n0, _ = nearest_points(river_union, cp)
            dx = float(cp.x - n0.x)
            dy = float(cp.y - n0.y)
            mag = hypot(dx, dy)
            if mag <= 1e-6:
                continue
            offset = float(cfg.river_setback_m) + min(0.35 * spacing, 120.0)
            target = Vec2(float(n0.x + dx / mag * offset), float(n0.y + dy / mag * offset))
            add_seed(
                target,
                seed_kind="riverfront_block",
                must_attach_arterial=True,
                riverfront_bias_steps=riverfront_bias_steps_default,
            )
            if rng.random() < 0.35:
                tang = Vec2(-dy / mag, dx / mag)
                add_seed(
                    target + tang * float(rng.uniform(0.2, 0.5) * spacing),
                    seed_kind="riverfront_block",
                    must_attach_arterial=True,
                    riverfront_bias_steps=riverfront_bias_steps_default,
                )
            # Additional block-edge seed on the river-facing boundary to reduce centroid-only inland starts.
            try:
                b_exterior = getattr(block, "exterior", None)
                if b_exterior is not None:
                    bp, _ = nearest_points(b_exterior, river_union)
                    bx = float(bp.x)
                    by = float(bp.y)
                    bdx = float(bx - n0.x)
                    bdy = float(by - n0.y)
                    bmag = hypot(bdx, bdy)
                    if bmag > 1e-6:
                        edge_seed = Vec2(
                            float(n0.x + bdx / bmag * (float(cfg.river_setback_m) + min(0.18 * spacing, 60.0))),
                            float(n0.y + bdy / bmag * (float(cfg.river_setback_m) + min(0.18 * spacing, 60.0))),
                        )
                        add_seed(
                            edge_seed,
                            seed_kind="riverfront_block",
                            must_attach_arterial=True,
                            riverfront_bias_steps=riverfront_bias_steps_default + 1,
                        )
            except Exception:
                pass
    return seeds


class ClassicRoadGenerator:
    def __init__(
        self,
        *,
        extent_m: float,
        probe: TerrainProbe,
        nodes: Sequence[object],
        edges: Sequence[object],
        hubs: Sequence[object],
        blocks: Optional[Sequence[object]],
        cfg: ClassicMajorLocalConfig,
        seed: int,
        stream_cb: Optional[StreamCallback] = None,
    ) -> None:
        self.extent_m = float(extent_m)
        self.probe = probe
        self.nodes = nodes
        self.edges = edges
        self.hubs = list(hubs or [])
        self.blocks = blocks or []
        self.cfg = cfg
        self.rng = np.random.default_rng(int(seed) + 8407)
        self.forbidden_geom = _build_forbidden_geom(getattr(probe, "river_union", None), float(cfg.river_setback_m))
        self.stream_cb = stream_cb

        self.base_segments = _flatten_segments_from_edges(edges, nodes, road_classes={"arterial", "major_local", "minor_local"})
        self.arterial_segments = _flatten_segments_from_edges(edges, nodes, road_classes={"arterial"})
        self.runtime_segments: list[Segment] = []
        self.queue: list[_QueueState] = []
        self.trace_count = 0
        self.branch_enqueued = 0
        self.culdesac_count = 0
        self.seed_kind_counts: dict[str, int] = {}
        self.riverfront_trace_count = 0
        self.arterial_t_attach_count = 0
        self.network_attach_fallback_count = 0
        self.failed_arterial_attach_count = 0

    def _blend(self, a: Vec2, b: Vec2, w_b: float) -> Vec2:
        if a.length() <= 1e-9:
            return b.normalized()
        if b.length() <= 1e-9:
            return a.normalized()
        bb = b
        if a.dot(bb) < 0.0:
            bb = Vec2(-bb.x, -bb.y)
        w = max(0.0, min(1.0, float(w_b)))
        d = Vec2(a.x * (1.0 - w) + bb.x * w, a.y * (1.0 - w) + bb.y * w)
        return d.normalized() if d.length() > 1e-9 else a.normalized()

    def _push_state(
        self,
        pos: Vec2,
        direction: Vec2,
        depth: int,
        priority: float,
        *,
        seed_kind: str = "arterial_portal",
        must_attach_arterial: bool = False,
        arterial_attach_budget_steps: int = 0,
        riverfront_bias_steps_remaining: int = 0,
        arterial_attached: bool = False,
    ) -> None:
        if len(self.queue) >= int(self.cfg.classic_max_queue_size):
            return
        d = direction.normalized()
        if d.length() <= 1e-9:
            return
        heapq.heappush(
            self.queue,
            _QueueState(
                priority=priority,
                pos=pos,
                direction=d,
                depth=depth,
                seed_kind=str(seed_kind),
                must_attach_arterial=bool(must_attach_arterial),
                arterial_attach_budget_steps=int(max(0, arterial_attach_budget_steps)),
                riverfront_bias_steps_remaining=int(max(0, riverfront_bias_steps_remaining)),
                arterial_attached=bool(arterial_attached),
            ),
        )

    def _seed_initial_states(self) -> list[ClassicSeed]:
        seeds = seed_classic_portals(
            extent_m=self.extent_m,
            nodes=self.nodes,
            edges=self.edges,
            blocks=self.blocks,
            river_union=getattr(self.probe, "river_union", None),
            cfg=self.cfg,
            seed=int(self.rng.integers(0, 2**31 - 1)),
        )
        step_m = max(6.0, float(self.cfg.classic_probe_step_m))
        default_attach_budget = max(5, int(round(max(5.0, 7.0 * float(self.cfg.classic_seed_spacing_m) / max(step_m, 1e-6)) / 7.0)))
        for s in seeds:
            p = s.pos
            if self.probe.check_water_hit(p):
                continue
            self.seed_kind_counts[s.seed_kind] = self.seed_kind_counts.get(s.seed_kind, 0) + 1
            art_tan, art_dist = _nearest_segment_tangent(p, self.arterial_segments)
            _, art_proj = _nearest_road_distance_and_projection(p, self.arterial_segments) if self.arterial_segments else (float("inf"), None)
            river_tan, river_dist = self.probe.nearest_river_bank_tangent(p)
            base = art_tan or river_tan or _nearest_hub_vector(p, self.hubs) or Vec2(1.0, 0.0)
            if river_tan is not None and river_dist < float(self.cfg.river_snap_dist_m) * 4.0:
                base = self._blend(base, river_tan, min(0.75, float(self.cfg.river_parallel_bias_weight) * 0.55))
            # Prefer major_local emergence roughly orthogonal to arterials.
            if art_tan is not None and art_dist < float(self.cfg.classic_seed_spacing_m):
                perp = Vec2(-art_tan.y, art_tan.x)
                if self.rng.random() < 0.7:
                    base = perp if self.rng.random() < 0.5 else Vec2(-perp.x, -perp.y)
            jitter = float(self.rng.uniform(-22.0, 22.0))
            d0 = _turn_vec(base, jitter)
            attach_budget = default_attach_budget + (3 if s.must_attach_arterial else 0)
            self._push_state(
                p,
                d0,
                depth=0,
                priority=float(self.rng.uniform(0.0, 1.0)),
                seed_kind=s.seed_kind,
                must_attach_arterial=s.must_attach_arterial,
                arterial_attach_budget_steps=attach_budget,
                riverfront_bias_steps_remaining=int(s.riverfront_bias_steps),
            )
            # A second seed direction adds more organic spread.
            self._push_state(
                p,
                _turn_vec(d0, 180.0 + float(self.rng.uniform(-18.0, 18.0))),
                depth=0,
                priority=float(self.rng.uniform(0.05, 1.2)),
                seed_kind=s.seed_kind,
                must_attach_arterial=s.must_attach_arterial,
                arterial_attach_budget_steps=attach_budget,
                riverfront_bias_steps_remaining=max(0, int(s.riverfront_bias_steps) - 1),
            )
            # Ensure at least one seed from block interiors tends to reconnect to the arterial network.
            if art_proj is not None and p.distance_to(art_proj) > max(8.0, 0.75 * float(self.cfg.classic_probe_step_m)):
                connect_dir = (art_proj - p).normalized()
                if connect_dir.length() > 1e-9:
                    self._push_state(
                        p,
                        _turn_vec(connect_dir, float(self.rng.uniform(-12.0, 12.0))),
                        depth=0,
                        priority=float(self.rng.uniform(-0.2, 0.2)),
                        seed_kind=s.seed_kind,
                        must_attach_arterial=bool(s.must_attach_arterial),
                        arterial_attach_budget_steps=attach_budget + 2,
                        riverfront_bias_steps_remaining=max(0, int(s.riverfront_bias_steps) - 2),
                    )
        return seeds

    def _maybe_enqueue_branches(
        self,
        pos: Vec2,
        direction: Vec2,
        depth: int,
        slope_deg: float,
        *,
        seed_kind: str,
        riverfront_bias_steps_remaining: int,
    ) -> None:
        if depth >= 10:
            return
        # Depth-based probability decay: branches become less likely further
        # from the originating arterial seed, preventing infinite sprawl.
        max_depth = 10
        decay_power = float(self.cfg.classic_depth_decay_power)
        depth_decay = max(0.0, (1.0 - float(depth) / float(max_depth))) ** decay_power
        base_prob = float(self.cfg.classic_branch_prob) * depth_decay
        if slope_deg > float(self.cfg.slope_serpentine_threshold_deg):
            base_prob *= 0.65
        elif slope_deg < float(self.cfg.slope_straight_threshold_deg):
            base_prob *= 1.05
        if self.rng.random() > base_prob:
            return
        for sign in (-1.0, 1.0):
            if self.rng.random() > (0.55 if sign < 0 else 0.75):
                continue
            ang = sign * float(self.rng.uniform(65.0, 105.0))
            bdir = _turn_vec(direction, ang)
            pri = float(depth + 1) + float(self.rng.uniform(0.0, 0.75))
            self._push_state(
                pos,
                bdir,
                depth + 1,
                pri,
                seed_kind=seed_kind,
                must_attach_arterial=False,
                arterial_attach_budget_steps=0,
                riverfront_bias_steps_remaining=max(0, int(riverfront_bias_steps_remaining) - 2),
            )
            self.branch_enqueued += 1

    def _nearest_arterial_probe(self, p: Vec2) -> tuple[float, Optional[Vec2]]:
        return _nearest_road_distance_and_projection(p, self.arterial_segments)

    def _nearest_runtime_major_local_probe(self, p: Vec2) -> tuple[float, Optional[Vec2]]:
        return _nearest_road_distance_and_projection(p, self.runtime_segments)

    def _nearest_any_network_probe(self, p: Vec2) -> tuple[float, Optional[Vec2]]:
        return _nearest_road_distance_and_projection(p, self.base_segments + self.runtime_segments)

    def _trace(self, state: _QueueState) -> Optional[_Trace]:
        step_m = max(6.0, float(self.cfg.classic_probe_step_m))
        max_steps = max(4, int(float(self.cfg.classic_max_trace_len_m) / step_m) + 2)
        points: list[Vec2] = [state.pos]
        prev_dir = state.direction.normalized()
        total_len = 0.0
        connected = 0
        culdesac = False
        reason = "max_steps"
        junction_probe = max(10.0, 0.9 * step_m)
        weak_progress_steps = 0
        arterial_attached = bool(state.arterial_attached)
        attach_budget_remaining = int(max(0, state.arterial_attach_budget_steps))
        riverfront_bias_steps_remaining = int(max(0, state.riverfront_bias_steps_remaining))
        network_attach_fallback = False
        arterial_t_attached = False

        if self.base_segments:
            d0, _ = _nearest_road_distance_and_projection(state.pos, self.base_segments)
            if d0 < junction_probe:
                connected += 1

        for step_i in range(max_steps):
            cur = points[-1]
            slope_deg = self.probe.sample_slope_deg(cur)
            if slope_deg > float(self.cfg.slope_serpentine_threshold_deg):
                d = self.probe.choose_serpentine_direction(cur, prev_dir, step_m, rng=self.rng)
            else:
                d = self.probe.adjust_direction_for_slope(cur, prev_dir, road_class="major_local")

            # Classical local heuristics: river parallel bias + arterial alignment + weak hub seek.
            river_tan, river_dist = self.probe.nearest_river_bank_tangent(cur)
            if river_tan is not None and river_dist < float(self.cfg.river_snap_dist_m) * 5.0:
                w = min(0.8, float(self.cfg.river_parallel_bias_weight) * (1.0 - river_dist / max(self.cfg.river_snap_dist_m * 5.0, 1e-6)))
                d = self._blend(d, river_tan, w)
            if riverfront_bias_steps_remaining > 0 and river_tan is not None:
                bank_proj, bank_dist = self.probe.nearest_river_bank_projection(cur)
                if bank_proj is not None:
                    away = (cur - bank_proj).normalized()
                else:
                    away = Vec2(0.0, 0.0)
                if river_dist < float(self.cfg.river_setback_m) + 8.0 and away.length() > 1e-9:
                    d = self._blend(d, away, 0.55)
                elif river_dist < float(self.cfg.river_snap_dist_m) * 8.0:
                    d = self._blend(d, river_tan, min(0.92, 0.55 + 0.05 * riverfront_bias_steps_remaining))
                    if bank_proj is not None and bank_dist > float(self.cfg.river_snap_dist_m) * 2.2:
                        to_bank = (bank_proj - cur).normalized()
                        if to_bank.length() > 1e-9:
                            d = self._blend(d, to_bank, 0.18)
            art_tan, art_dist = _nearest_segment_tangent(cur, self.arterial_segments)
            if art_tan is not None and art_dist < float(self.cfg.classic_seed_spacing_m) * 2.0 and total_len >= 1.5 * step_m:
                w = min(0.65, float(self.cfg.arterial_align_weight) * (1.0 - art_dist / max(self.cfg.classic_seed_spacing_m * 2.0, 1e-6)))
                d = self._blend(d, art_tan, w)
            hub_vec = _nearest_hub_vector(cur, self.hubs)
            if hub_vec is not None and total_len >= 1.0 * step_m:
                d = self._blend(d, hub_vec, min(0.35, float(self.cfg.hub_seek_weight)))

            d = _clamp_turn(prev_dir, d, float(self.cfg.classic_turn_limit_deg))
            if d.length() <= 1e-9:
                reason = "zero_dir"
                break

            nxt = Vec2(cur.x + d.x * step_m, cur.y + d.y * step_m)
            if not (0.0 <= nxt.x <= self.extent_m and 0.0 <= nxt.y <= self.extent_m):
                reason = "boundary"
                break

            if self.probe.check_water_hit(nxt) or _point_in_forbidden_geom(nxt, self.forbidden_geom):
                alt_dir = self.probe.snap_or_bias_to_riverfront(cur, d)
                alt_dir = _clamp_turn(prev_dir, alt_dir, float(self.cfg.classic_turn_limit_deg))
                alt_nxt = Vec2(cur.x + alt_dir.x * step_m, cur.y + alt_dir.y * step_m)
                if (
                    alt_dir.length() <= 1e-9
                    or not (0.0 <= alt_nxt.x <= self.extent_m and 0.0 <= alt_nxt.y <= self.extent_m)
                    or self.probe.check_water_hit(alt_nxt)
                    or _point_in_forbidden_geom(alt_nxt, self.forbidden_geom)
                ):
                    reason = "river_blocked"
                    break
                d = alt_dir
                nxt = alt_nxt

            # Distance-from-arterial constraint: stop major_local growth that
            # wanders too far from the arterial network to prevent infinite sprawl.
            max_art_dist = float(self.cfg.classic_max_arterial_distance_m)
            if max_art_dist > 0.0 and self.arterial_segments:
                d_art_sprawl, _ = self._nearest_arterial_probe(nxt)
                if d_art_sprawl > max_art_dist:
                    reason = "arterial_too_far"
                    break

            new_seg = Segment(cur, nxt)
            # Self intersection (ignore adjacent segments).
            if len(points) >= 4:
                hit_self = False
                for i in range(len(points) - 3):
                    prior = Segment(points[i], points[i + 1])
                    hit = segment_intersection(new_seg, prior)
                    if hit.kind in ("point", "overlap"):
                        hit_self = True
                        break
                if hit_self:
                    reason = "self_intersection"
                    break

            # Snap/stop near network to feed intersection operators later.
            min_snap_len = max(20.0, 1.25 * step_m)
            can_snap = total_len >= min_snap_len
            need_arterial = (not arterial_attached) and (attach_budget_remaining > 0)
            must_arterial = bool(state.must_attach_arterial) and (not arterial_attached) and (attach_budget_remaining > 0)
            d_art, proj_art = self._nearest_arterial_probe(nxt)
            if can_snap and d_art < junction_probe and proj_art is not None:
                if proj_art.distance_to(cur) > 2.0:
                    points.append(proj_art)
                    total_len += cur.distance_to(proj_art)
                    connected += 1
                arterial_attached = True
                arterial_t_attached = True
                reason = "near_arterial_t"
                break

            points.append(nxt)
            total_len += cur.distance_to(nxt)
            prev_dir = d

            # Turtle step streaming event: emit growing polyline each step
            _emit_stream_event(self.stream_cb, {
                "event_type": "road_trace_progress",
                "data": {
                    "trace_id": f"major_local-trace-{self.trace_count}",
                    "points": [{"x": p.x, "y": p.y} for p in points],
                    "complete": False,
                    "road_class": "major_local",
                },
            })

            attach_budget_remaining = max(0, attach_budget_remaining - 1)
            if riverfront_bias_steps_remaining > 0:
                riverfront_bias_steps_remaining -= 1

            if step_i > 0 and (step_i % 2 == 0):
                self._maybe_enqueue_branches(
                    nxt,
                    d,
                    state.depth + min(step_i // 3, 6),
                    slope_deg,
                    seed_kind=state.seed_kind,
                    riverfront_bias_steps_remaining=riverfront_bias_steps_remaining,
                )

            # Allow cul-de-sac endings for sprawl feel.
            if total_len >= float(self.cfg.classic_min_trace_len_m):
                cont_prob = float(self.cfg.classic_continue_prob)
                if slope_deg > float(self.cfg.slope_serpentine_threshold_deg):
                    cont_prob *= 0.9
                # Distance-based continue probability decay: roads far from
                # arterials are more likely to terminate early.
                max_art_dist = float(self.cfg.classic_max_arterial_distance_m)
                if max_art_dist > 0.0 and self.arterial_segments:
                    d_art_cont, _ = self._nearest_arterial_probe(cur)
                    if d_art_cont > 0.5 * max_art_dist:
                        ratio = min(1.0, d_art_cont / max_art_dist)
                        cont_prob *= max(0.15, 1.0 - ratio)
                if self.rng.random() > cont_prob:
                    culdesac = self.rng.random() < float(self.cfg.classic_culdesac_prob)
                    reason = "stochastic_stop"
                    break

            if total_len >= float(self.cfg.classic_max_trace_len_m):
                reason = "max_len"
                break

        if len(points) >= 2 and points[-1].distance_to(points[-2]) <= 1e-6:
            points = points[:-1]

        if len(points) < 2:
            return None
        length = _polyline_length(points)
        if length < float(self.cfg.classic_min_trace_len_m):
            return None
        # Require at least one connection to the pre-existing network (or snapped endpoint).
        if connected < 1:
            d0, _ = self._nearest_any_network_probe(points[0])
            d1, _ = self._nearest_any_network_probe(points[-1])
            if d0 < junction_probe:
                connected += 1
            if d1 < junction_probe:
                connected += 1
            # Soft acceptance for substantial traces that terminate near the network; intersection operators
            # can cleanly snap them later.
            if connected < 1 and d1 < max(junction_probe * 2.5, 28.0):
                connected += 1
        if connected < 1:
            return None
        failed_arterial_attach = bool(state.must_attach_arterial) and not arterial_t_attached
        return _Trace(
            points=points,
            connection_count=int(connected),
            culdesac=bool(culdesac),
            reason=reason,
            seed_kind=str(state.seed_kind),
            arterial_t_attached=bool(arterial_t_attached),
            network_attach_fallback=bool(network_attach_fallback),
            failed_arterial_attach=bool(failed_arterial_attach),
        )

    def grow(self) -> tuple[list[list[Vec2]], list[bool], list[str], dict[str, float]]:
        seeds = self._seed_initial_states()
        traces: list[list[Vec2]] = []
        cul_flags: list[bool] = []
        notes = [f"classic_seed_count:{len(seeds)}"]
        riverfront_seed_count = sum(1 for s in seeds if str(s.seed_kind).startswith("riverfront_"))
        notes.append(f"classic_seed_riverfront:{riverfront_seed_count}")
        stop_reasons: dict[str, int] = {}

        while self.queue and len(traces) < int(self.cfg.classic_max_segments):
            state = heapq.heappop(self.queue)
            if self.probe.check_water_hit(state.pos):
                continue
            if self.runtime_segments:
                d_seed, _ = _nearest_road_distance_and_projection(state.pos, self.runtime_segments)
                if d_seed < 0.6 * float(self.cfg.classic_seed_spacing_m):
                    continue
            tr = self._trace(state)
            if tr is None:
                continue
            traces.append(tr.points)
            cul_flags.append(bool(tr.culdesac))
            # Stream the completed trace (use same trace_id as partial events)
            _emit_stream_event(self.stream_cb, {
                "event_type": "road_trace_progress",
                "data": {
                    "trace_id": f"major_local-trace-{self.trace_count}",
                    "points": [{"x": p.x, "y": p.y} for p in tr.points],
                    "complete": True,
                    "road_class": "major_local",
                    "culdesac": tr.culdesac,
                },
            })
            if tr.culdesac:
                self.culdesac_count += 1
            if str(tr.seed_kind).startswith("riverfront_"):
                self.riverfront_trace_count += 1
            if tr.arterial_t_attached:
                self.arterial_t_attach_count += 1
            if tr.network_attach_fallback:
                self.network_attach_fallback_count += 1
            if tr.failed_arterial_attach:
                self.failed_arterial_attach_count += 1
            self.runtime_segments.extend(_polyline_segments(tr.points))
            self.trace_count += 1
            stop_reasons[tr.reason] = stop_reasons.get(tr.reason, 0) + 1

        for reason, count in sorted(stop_reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:4]:
            notes.append(f"classic_stop:{reason}:{count}")
        notes.append(f"classic_attach:arterial_t:{self.arterial_t_attach_count}")
        notes.append(f"classic_attach:fallback_network:{self.network_attach_fallback_count}")
        notes.append(f"classic_trace_count:{len(traces)}")
        numeric = {
            "major_local_classic_enabled": 1.0,
            "major_local_classic_trace_count": float(len(traces)),
            "major_local_classic_culdesac_count": float(self.culdesac_count),
            "major_local_classic_branch_enqueued_count": float(self.branch_enqueued),
            "major_local_classic_riverfront_seed_count": float(riverfront_seed_count),
            "major_local_classic_riverfront_trace_count": float(self.riverfront_trace_count),
            "major_local_classic_arterial_t_attach_count": float(self.arterial_t_attach_count),
            "major_local_classic_network_attach_fallback_count": float(self.network_attach_fallback_count),
            "major_local_classic_failed_arterial_attach_count": float(self.failed_arterial_attach_count),
        }
        return traces, cul_flags, notes, numeric


def generate_classic_major_local(
    *,
    extent_m: float,
    height: Optional[np.ndarray],
    slope: np.ndarray,
    river_mask: np.ndarray,
    river_areas: Optional[Sequence[object]],
    river_union: object,
    nodes: Sequence[object],
    edges: Sequence[object],
    hubs: Sequence[object],
    blocks: Optional[Sequence[object]],
    cfg: ClassicMajorLocalConfig,
    seed: int,
    stream_cb: Optional[StreamCallback] = None,
) -> tuple[list[list[Vec2]], list[bool], list[str], dict[str, float]]:
    probe = TerrainProbe(
        extent_m=float(extent_m),
        height=height,
        slope=slope,
        river_mask=river_mask,
        river_areas=river_areas,
        river_union=river_union,
        cfg=TerrainProbeConfig(
            slope_straight_threshold_deg=float(cfg.slope_straight_threshold_deg),
            slope_serpentine_threshold_deg=float(cfg.slope_serpentine_threshold_deg),
            slope_hard_limit_deg=float(cfg.slope_hard_limit_deg),
            contour_follow_weight=float(cfg.contour_follow_weight),
            river_snap_dist_m=float(cfg.river_snap_dist_m),
            river_parallel_bias_weight=float(cfg.river_parallel_bias_weight),
            river_avoid_weight=float(cfg.river_avoid_weight),
            river_setback_m=float(cfg.river_setback_m),
        ),
    )
    gen = ClassicRoadGenerator(
        extent_m=float(extent_m),
        probe=probe,
        nodes=nodes,
        edges=edges,
        hubs=hubs,
        blocks=blocks,
        cfg=cfg,
        seed=int(seed),
        stream_cb=stream_cb,
    )
    return gen.grow()
