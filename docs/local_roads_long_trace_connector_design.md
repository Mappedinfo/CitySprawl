# Local Roads Long-Trace + Connector Design (V1)

## 1. 背景与问题

当前 `classic_sprawl` local 生成已经具备 block 内乌龟寻路、major portal seeds、terrain/river 约束与 local reroute，但仍存在两个结构性问题：

- local 主线语义 trace 偏短（历史目标偏 `500–1000m`）
- local 分支更多偏形态填充，local-local 连通提升不够稳定

同时需要强调：

- `local_blocks` 来源于冻结后的 major network（arterial + collector）
- `local_blocks` 是生成域，不应成为 local-local 连通补救的硬边界
- `trace continuity` 与最终 `local edge` 长度不是同一概念（最终 edge 会被拓扑拆分/切段）

## 2. 设计目标（V1）

1. 将 local `trace continuity` 默认 hard cap 提升到 `6km`
2. 保留乌龟寻路（turtle pathing）主逻辑
3. 对 root/shallow mainline 增加 `200–400m` 节奏的 `sub Local Roads`（左右分支）
4. `sub Local` 以连接两条 Local Roads 为目标（connector-seeking）
5. 若 trace 达到 6km 仍未连接其他 local，则在 `network.py` 后处理阶段进行端点连桥（A*）

## 3. 术语

- `trace continuity`: `classic_local_fill.py` 中语义上的连续生长 polyline（后续可能被拆成多条 edge）
- `mainline`: root/shallow 主线 role
- `sub_local_connector`: 由 mainline 里程调度器产生、以连接 local-local 为主的分支 role
- `fill_branch`: 现有格点式 branching 的填充分支 role
- `endpoint bridge`: `network.py` 在 pending local entries 层生成的端点连桥 local entry

## 4. 当前实现审查摘要（代码位置）

- `engine/roads/classic_local_fill.py`
  - major portal seeds 优先（非边界播种）
  - 乌龟寻路 + terrain/river/collector shaping
  - grid-crossed branching（保留）
  - 近 major detach/major-repel（已有）
- `engine/roads/network.py`
  - `pending_local_entries` 承载 classic/frontier/grid supplement 结果
  - optional local reroute 在 append 前执行
  - local append 会做长折线切段保护（因此 trace != final edge）

## 5. 新算法设计（V1）

## 5.1 长主线语义（trace continuity）

- 复用 `local_classic_max_trace_len_m` 作为 hard cap 语义，默认 `6000m`
- `mainline`:
  - 长主线目标区间：约 `1200–4800m`
  - 软上限：约 `5600m`
  - 硬上限：`6000m`
- `sub_local_connector`:
  - 以连接为目标，长度 cap 更短（默认 `<=1800m`）
- `fill_branch`:
  - 保持较短/中长度，避免全图 local 过密

## 5.2 乌龟寻路 + sub Local（200–400m 左右分支）

在保留现有 turtle 主体和 grid branching 的前提下，为 `mainline` 增加里程调度器：

- 触发对象：`branch_role == "mainline"` 且 `depth <= 1`
- 触发间隔：每次随机采样 `200–400m`
- 触发动作：从当前点生成左右两条 `sub_local_connector`（若单侧非法则只保留合法侧）
- 初始方向：接近正交（约 `±88°–92°`）

### sub Local 连接导向（connector-seeking）

`sub_local_connector` 在每步择向中增加 local-local 连接吸引项：

优先目标：
1. 不同 continuity 的 local endpoints
2. 不同 continuity 的 local segments 投影点
3. 若无可行目标，退回普通 turtle 行为

评分（概念）：
- `terrain_alignment`
- `river/water feasibility`
- `local_endpoint_attraction`
- `local_segment_attraction`
- `major_parallel_penalty`（避免贴着 major 平行跑）

## 5.3 连接状态拆分（network vs local）

为满足“超 6km 未与 Local Roads 连接”的判定，需要区分：

- `connected_network_count`
- `connected_local_count`
- （可选诊断）`connected_major_count`

`overlimit_unconnected_local` 判定条件（trace 接受后）：

- `reached_trace_cap == True`
- `trace_len_m >= 6000m - tolerance`
- `connected_local_count == 0`

## 5.4 Endpoint Bridge（`network.py` 后处理）

插入位置：
- local reroute 之后
- append local edges 之前

处理流程：
1. 从 `pending_local_entries` 构建全局 local endpoint 池
2. 筛选 `is_overlimit_unconnected_candidate == True` 的 trace（优先最长）
3. 在全局 endpoint 池中贪心配对（不按 `local_blocks` 硬限制）
4. 使用 local A* (`_route_points_with_cost_mask(..., road_class="local")`) 生成连桥 polyline
5. 追加为新的 `pending_local_entry`
   - `flags`: `local_endpoint_bridge`
   - `meta.branch_role = "endpoint_bridge"`
   - continuity 使用新前缀（如 `local-link-cont-*`）

失败时安全降级：
- 记录 notes / numeric
- 不中断主流程

## 6. 伪代码（简化）

```python
for mainline in local_traces:
    while turtle_grow():
        if mileage >= next_sub_trigger_m:
            spawn_sub_connector(left)
            spawn_sub_connector(right)
            next_sub_trigger_m += rand(200, 400)

for sub_connector in queue:
    d = terrain_direction(...)
    d = blend_with_local_endpoint_or_segment_attraction(d, runtime_local, existing_local)
    d = avoid_parallel_major(d, arterial+collector)
    step()

for accepted_trace in traces:
    if reached_6km and connected_local_count == 0:
        mark_overlimit_unconnected_candidate(meta)

pending_local_entries = reroute(pending_local_entries)

for entry in overlimit_unconnected_candidates:
    target = choose_best_other_local_endpoint(global_pool)
    route = astar_local(entry.endpoint, target.endpoint)
    if route:
        pending_local_entries.append(local_endpoint_bridge(route))
```

## 7. `trace` vs 最终 `edge`

本设计中的 `6km` 是 **trace continuity 上限**，不是最终单条 edge 长度目标。

后续 `network.py::_append_polyline_edge(...)` 仍可能对 local polyline 切段，以保证：

- 几何稳定性
- 交叉口切分质量
- 拓扑后处理（intersections / syntax）安全

因此评估“长主线”应优先看 trace 级指标，而不是仅看最终 edge 长度分布。

## 8. 指标与验收（建议）

`classic_local_fill` 内部 numeric（示例）：

- `local_classic_long_trace_cap_m`
- `local_classic_trace_over_1km_rate`
- `local_classic_trace_over_3km_rate`
- `local_classic_trace_over_6km_count`
- `local_classic_trace_reached_cap_count`
- `local_classic_trace_overlimit_unconnected_count`
- `local_classic_sub_branch_trigger_count`
- `local_classic_sub_branch_left_spawn_count`
- `local_classic_sub_branch_right_spawn_count`
- `local_classic_sub_branch_connector_touch_count`
- `local_classic_local_touch_count_total`

`network.py` endpoint bridge numeric（示例）：

- `local_endpoint_bridge_candidate_count`
- `local_endpoint_bridge_attempt_count`
- `local_endpoint_bridge_success_count`
- `local_endpoint_bridge_failed_count`
- `local_endpoint_bridge_avg_length_m`
- `road_hierarchy_local_endpoint_bridge_ms`

## 9. 风险与控制

风险：
- 长主线 + 高频 sub-branch 可能提高 local 密度与运行时间
- endpoint bridge 全局配对可能产生不自然的长连接
- 跨 major-defined blocks 的连桥若约束不足可能出现视觉违和

控制：
- `sub_local_connector` 单独长度 cap / 深度 cap
- endpoint bridge 最大配对距离限制 + A* 失败安全降级
- 端点锁定（避免重复连桥）
- 保留 local polyline 切段保护

## 10. 回滚策略

建议分阶段提交：

1. 文档修正 + 新设计文档
2. `classic_local_fill` 长主线 + sub Local
3. `network.py` endpoint bridge

如 endpoint bridge 效果不理想，可单独回滚第 3 步，同时保留长主线与 sub Local 改进。
