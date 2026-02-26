# 道路生成逻辑审查文档（按生成次序，静态代码审查）

说明：本文基于源码静态审查（未运行样例），按真实执行顺序组织，不按重要性排序。已按“全系统口径”覆盖核心路网、步行通道和前端兼容显示类，并标注重复生成/重复输出/去重/替换机制。

## 1. 审查范围与口径

### 1.1 全系统口径定义
全系统口径包含两层：

1. 核心道路网络（最终落在 `artifact.roads.edges`）
2. 步行通道（最终落在 `artifact.pedestrian_paths`，不在 `artifact.roads.edges`）
3. 前端兼容显示类（如 `service`，用于渲染/图层判定，但当前静态审查未发现后端引擎生成写入点）

关键源码定位：
- `generate_roads` 三阶段主流程：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3047`
- `artifact.pedestrian_paths` 接入：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/generator.py:940`
- 前端 `service/pedestrian` 显示兼容：`/Users/shiqi/Coding/github/GIStudio/CityGen/web/src/render/cityRenderer.ts:27`，`/Users/shiqi/Coding/github/GIStudio/CityGen/web/src/render/stageRenderer.ts:485`

### 1.2 本文结论先给出（你关心的数量）
- 核心路网 `road_class`（引擎真实生成到 `artifact.roads.edges`）：**3 类**
- 分别是：`arterial`、`collector`、`local`
- 步行通道：**1 类（`pedestrian_paths`，独立列表）**
- 前端兼容显示类：**1 类（`service`，当前未发现引擎生成）**

因此：
- 核心路网口径数量：**3**
- 全系统道路/通道口径数量（含步行通道 + service 兼容类）：**5**
- 其中引擎真实生成的通道类目数量（含 `pedestrian_paths`）：**4**

### 1.3 易混定义（先澄清）
- `branch` 不是 `road_class`，是早期分支节点的 `kind` 语义之一；但分支阶段新建的边被标成 `local`。证据：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:839`
- `pedestrian_paths` 不属于 `artifact.roads.edges`，而是在地块/parcel 阶段单独写入 `artifact.pedestrian_paths`。证据：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/generator.py:995`
- `service` 在前端样式与图层判断中被识别，但当前未见引擎 `road_class="service"` 写入逻辑（静态搜索引擎目录无命中；前端兼容点见上文引用）

---

## 2. 核心数据结构与变量命名（总览）

### 2.1 输入配置变量（对外）
`RoadsConfig` 定义于 `engine.models`，包含候选图、主干/次干/本地路、reroute、交叉口、syntax、风格等参数。证据：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/models.py:34`

### 2.2 内部核心产物（引擎内）
定义于 `engine/roads/network.py`：
- `BuiltRoadNode`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:24`
- `BuiltRoadEdge`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:32`
- `RoadBuildResult`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:50`
- `FrozenMajorNetwork`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:58`

### 2.3 对外序列化产物（Artifact）
定义于 `engine/models.py`：
- `RoadNodeRecord`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/models.py:231`
- `RoadEdgeRecord`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/models.py:238`
- `RoadNetwork`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/models.py:254`

### 2.4 字段级差异（重要）
`BuiltRoadEdge`（内部）有：
- `flags`
- `continuity_id`
- `parent_continuity_id`
- `segment_order`

`RoadEdgeRecord`（对外）保留：
- `continuity_id`
- `parent_continuity_id`
- `segment_order`

但 **不包含 `flags`**。`flags` 仅在内部流程（去重、局部语义、指标）使用。转换证据：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/generator.py:556`

### 2.5 步行通道相关
- 宽度/渲染预设常量（包含 `pedestrian`）：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/pedestrian.py:3`
- `ROAD_RENDER_ORDER` / `ROAD_WIDTH_PRESET`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/pedestrian.py:8`
- 实际步行通道生成算法在地块模块，不在 `engine/roads` 主生成链路：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/blocks/parcelize.py:82`

---

## 3. 按生成次序的道路生成流程（主文档主体）

以下严格按执行顺序。

### 3.1 步骤 1：生成入口组装（`engine/generator.py` 调用 `generate_roads`）
**位置**  
`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/generator.py:369`

**关键变量名**  
`roads_cfg`, `collector_generator_value`, `road_progress_cb`, `_road_progress_canonical`, `road_result`

**输入**  
`config.roads`（`RoadsConfig`），`hub_result.hubs`，`terrain_bundle.height/slope/hydrology.river_mask`，`river_areas`，`seed`

**算法/策略**  
入口层做参数透传与少量兼容映射：
- `collector_generator` 的遗留值 `tensor_streamline` 被静默映射为 `classic_turtle`
- 将总进度条映射到道路子进度区间
- 将 `RoadsConfig` 参数完整传入 `engine.roads.generate_roads(...)`

**输出**  
`road_result`（`RoadBuildResult`），包含 `nodes`、`edges`、`candidate_debug`、`metrics`

**依赖**  
`generate_roads`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3047`

**重复标注**  
无（仅入口组装）

**备注**  
非新增道路；是参数编排层。

---

### 3.2 步骤 2：候选图构建（Hub kNN + 成本评估）
**位置**  
`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:668`（`_build_candidate_graph`）  
成本函数：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:141`（`_segment_cost`）

**关键变量名**  
`graph`, `candidate_debug`, `k_neighbors`, `slope_penalty`, `river_cross_penalty`

**输入**  
`hubs`, `extent_m`, `slope`, `river_mask`, `k_neighbors`, `slope_penalty`, `river_cross_penalty`

**算法/策略**  
1. 对 Hub 建 `networkx.Graph`
2. 每个 hub 连接到 k 个最近邻（kNN）
3. 每条候选边调用 `_segment_cost` 计算权重：
- 沿线段离散采样
- 计算平均坡度归一化项
- 统计河流穿越次数（布尔进出切换近似）
- 权重公式：`length * (1 + slope_penalty * slope_norm) + river_crossings * river_cross_penalty`
4. 若图不连通，迭代添加跨连通分量的最小代价桥边，保证连通

**输出**  
- 候选图 `graph`
- 调试候选边 `candidate_debug`（用于 debug_layers）

**依赖**  
`networkx`、`_segment_cost`、地形/河流栅格

**重复标注**  
`重复生成风险：低`（候选图阶段可能存在双向枚举候选，但通过 `graph.has_edge(...)` 避免重复插入同一候选边）

**备注**  
这一步还不是最终道路，只是候选拓扑。

---

### 3.3 步骤 3：主干骨架选择（MST + loop enhancement）
**位置**  
`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:749`（`_generate_backbone_edges`）

**关键变量名**  
`selected_backbone`, `loop_budget`, `tree`, `candidates`, `gain`

**输入**  
候选图 `graph`，`loop_budget`

**算法/策略**  
1. 先做最小生成树（MST）
2. 在非树边中评估“绕行收益”：
- `gain = (detour - direct) / direct`
- 按收益排序
- 仅当 `gain > 0.10` 才加回环路
- 加入数量受 `loop_budget` 限制

**输出**  
主干骨架边集合（后续转为 `arterial`）

**依赖**  
`networkx.minimum_spanning_tree`，`networkx.shortest_path_length`

**重复标注**  
无（输出是选边结果，不直接写 `edges` 时才可能重复）

**备注**  
这是 `arterial` 拓扑来源。

---

### 3.4 步骤 4：分支生成（注意：这里会先产生 `local` 类分支边）
**位置**  
`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:839`（`_generate_branches`）

**关键变量名**  
`branch_steps`, `nodes`, `edges`, `node_lookup`, `base_angle`, `step_len`, `_branch_direction_candidates`, `_branch_step_cost`

**输入**  
当前 `nodes`（hub 节点）、`edges`（已选 arterial 边）、地形/河流、`branch_steps`, `seed`

**算法/策略**  
1. 对非 T1 hub（跳过 `tier==1`）生成外向分支
2. 从城市中心指向 hub 的方向作为 `base_angle`
3. 每步尝试多个角度偏移候选（带随机扰动）
4. `_branch_step_cost` 过滤非法候选：
- 越界
- 过近已有节点
- 与已有边发生非法相交
5. 从合法候选中选最小成本
6. 新建 `BuiltRoadNode(kind="branch")` 与新边

**输出**  
直接向 `edges` 追加边，且新边 `road_class="local"`（这是关键）

**依赖**  
`_branch_step_cost`、`_segment_cost`、几何相交判定

**重复标注**  
`重复生成风险：中`  
原因：后续 Phase 3 的 local 主流程也会继续生成 `local`。这里的 `local` 与 Phase 3 `local` 是同一 `road_class`，可能在空间上重叠或在端点上最终被去重。

**备注**  
这是最容易误判的点：`local` 并非只在 Phase 3 才出现。

---

### 3.5 步骤 5：初次去重与节点吸附（`_dedupe_and_snap`）
**位置**  
`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:2747`

**关键变量名**  
`buckets`, `node_alias`, `seen_pairs`, `duplicate_count`, `zero_count`, `pair`

**输入**  
当前 `nodes`, `edges`

**算法/策略**  
1. 节点按网格桶吸附（`snap_tol` 默认 0.5m）
2. 边去重键为：`sorted(u,v) + road_class`
3. 遇到重复边时：
- 保留已有边几何/属性
- 合并 `flags`
- 合并连续性 ID（优先非空）
4. `u==v` 的零长度边直接丢弃

**输出**  
- `deduped_nodes`, `deduped_edges`
- 统计：`duplicate_edge_count`, `zero_length_edge_count`

**依赖**  
内部 `flags/continuity` 合并辅助函数

**重复标注**  
`最终去重消解（第一次）`  
这一步开始显式消解步骤 3/4 产生的端点级重复。

**备注**  
这里的去重只按端点+路类，不按几何形状；平行重叠线段不会被该键消解。

---

### 3.6 步骤 6：初次路由（A* 栅格 + RDP 简化）
**位置**  
A*：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:364`  
点对点代价路由：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:461`  
全边路由：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:599`

**关键变量名**  
`route_res`, `slope_grid`, `river_grid`, `slope_norm`, `allowed_mask`, `cells`, `path_points`

**输入**  
`nodes`, `edges`, `slope`, `river_mask`, `extent_m`, 成本权重参数

**算法/策略**  
1. 将坡度/河流栅格降采样到路由分辨率（约 96~192）
2. 使用 8 邻域 A*：
- 步长成本（直/斜）
- 坡度二次惩罚（`slope_norm^2`）
- 河流惩罚（按道路等级不同）
3. 生成折线后进行去重
4. 用 RDP 简化折线（保留端点）

**输出**  
为 `arterial/collector/local` 边补齐或更新 `path_points`，并计算 `weight/length_m/river_crossings`

**依赖**  
A*、RDP、栅格映射

**重复标注**  
无新增，仅几何生成/重算

**备注**  
这一步是“几何生成”，不是新增拓扑边。

---

### 3.7 步骤 7：Arterial 交叉口清理（`intersections`）
**位置**  
调用点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3324`  
实现：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/intersections.py:518`

**关键变量名**  
`target_classes={"arterial"}`, `snap_radius_m`, `t_junction_radius_m`, `split_tolerance_m`, `min_dangle_length_m`, `inter_notes`, `inter_numeric`

**输入**  
`nodes`, `edges`（仅处理 arterial 子集），交叉口参数

**算法/策略**  
`apply_intersection_operators` 在目标类子集上执行：
1. 端点吸附到已有节点（`snap_endpoints_to_nodes`）
2. 端点吸附到线段并创建 T 口（函数内部）
3. 交叉切分（`split_crossings`）
4. 裁剪短悬垂段（`prune_short_dangles`）
5. 再与非目标类边合并回总边集

**输出**  
更新后的 `nodes`, `edges`，以及 `inter_notes`/`inter_numeric`（以 `arterial_` 前缀汇总）

**依赖**  
`intersections.py` 四类算子

**重复标注**  
`非重复但会改变数量/形态`  
可能切分/删除/吸附，改变边数量与几何，但不是“重复生成”。

**备注**  
这是 Phase 1 的后处理清理。

---

### 3.8 步骤 8：Arterial 流式重发（只重发预览，不是新增道路）
**位置**  
重发函数：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:86`  
调用点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3346`

**关键变量名**  
`_emit_stream_polyline_snapshot`, `road_classes={"arterial"}`

**输入**  
当前 `edges`（已清理后的 arterial 几何）

**算法/策略**  
将现有 arterial `path_points` 重新作为流式事件 `road_polyline_added` 发给前端，以便实时预览在最终 artifact 到达前能看到主干骨架。

**输出**  
仅流式事件；不改 `nodes/edges`

**依赖**  
`stream_cb`

**重复标注**  
`重复输出（预览）`  
这是同一路段的“重发显示”，不是重复道路生成。

**备注**  
必须与“新增道路”区分。

---

### 3.9 步骤 9：Collector 生成（Phase 2）
**位置**  
调度：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3352`  
核心函数：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:1599`（`_generate_hierarchy_linework`）  
classic backend：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/classic_growth.py:903`

**关键变量名**  
`generation_phase="collector_only"`, `collector_generator`, `collector_backend`, `collector_blocks`, `collector_added`, `notes`, `numeric`

**输入**  
已清理的 arterial 网络、地形/河流、风格、collector classic/grid 参数、`hubs`

**算法/策略**  
在 `_generate_hierarchy_linework` 内：
1. 从 arterial 网络提取 macro blocks（供 collector 填充）
2. 选择 collector backend：
- `classic_turtle`（默认）
- `grid_clip`（回退或显式）
- 遗留 `tensor_streamline` 别名映射到 `classic_turtle`
3. `classic_turtle` 路径：
- `ClassicCollectorConfig` + `TerrainProbe`
- 多种 seed（含河岸偏置等）
- 队列/trace 生长，输出 traces 与 cul-de-sac 标记
- 每条 trace 用 `_append_polyline_edge(... road_class="collector")` 写入
4. `grid_clip` 路径：
- 在块内生成平行线（方向来自块主轴 + 风格扰动）
- 裁剪后写入 collector 边

**输出**  
向 `edges` 追加 `collector` 边；记录 `collector_*` notes/numeric（如 classic trace 数量、是否降级）

**依赖**  
`classic_growth.py`、块提取/几何裁剪、`TerrainProbe`

**重复标注**  
`重复生成风险：中`  
原因：collector 可能在 classic/grid fallback 逻辑边界、块边界裁剪和后续交叉口切分中出现重合/重复端点边，最终依赖后续去重与交叉口处理。

**备注**  
若 `road_style=="skeleton"`，`_generate_hierarchy_linework` 会直接返回，不生成层级路网（collector/local）。

---

### 3.10 步骤 10：Collector 交叉口清理（Phase 2b）
**位置**  
调用点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3363`  
实现：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/intersections.py:518`

**关键变量名**  
`target_classes={"collector"}`, `coll_inter_notes`, `coll_inter_numeric`

**输入**  
当前 `nodes`, `edges`，交叉口参数

**算法/策略**  
与步骤 7 相同的交叉口算子流程，但仅作用于 `collector`。

**输出**  
更新 `nodes`, `edges`，collector 交叉口指标并入 `inter_numeric`

**依赖**  
`intersections.py`

**重复标注**  
`非重复但会改变数量/形态`

**备注**  
清理后才冻结主干网络，避免 local 附着到后续会被删除的 collector（“ghost road”问题）。

---

### 3.11 步骤 11：冻结主干网络（Arterial + Collector）与 local block 预计算（Phase 2c）
**位置**  
调用点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3384`  
实现：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:1309`（`_freeze_major_network`）

**关键变量名**  
`frozen_major`, `frozen_major.edges`, `frozen_major.nodes`, `frozen_major.local_blocks`, `river_union`

**输入**  
当前 `nodes`, `edges`, `river_areas`, `collector_spacing_m`, `local_spacing_m`, `max_local_block_area_m2`

**算法/策略**  
1. 过滤并深拷贝 `arterial + collector` 为 Major Network 快照
2. 基于冻结后的主干几何预计算 `local_blocks`
3. 缓存 `river_union`
4. 将预计算结果打包成 `FrozenMajorNetwork`

**输出**  
`FrozenMajorNetwork`（供 Phase 3 local-only 使用）

**依赖**  
块提取逻辑、河流几何 union

**重复标注**  
无（快照/预计算）

**备注**  
这是三阶段解耦的关键，防止 local 依赖“未稳定”的 collector 几何。

---

### 3.12 步骤 12：Local 生成主流程（Phase 3）

**位置**  
调度：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3401`  
核心函数：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:1599`（`_generate_hierarchy_linework`，`generation_phase="local_only"`）

**关键变量名（总）**  
`generation_phase`, `frozen_major_network`, `local_blocks`, `pending_local_entries`, `local_backend`, `local_need_grid_supplement`, `local_need_coverage_supplement`, `local_grid_supplement_budget`, `notes`, `numeric`

**输入（总）**  
冻结后的 Major Network（含 `local_blocks`）、地形/河流、local classic 参数、local reroute 参数、风格参数、stream 回调

**算法/策略（总）**  
Local 阶段先在 `pending_local_entries` 中积累候选 local 折线，再统一追加为边；中间可能做补充生成、几何重路由和端点桥接。

**输出（总）**  
新增 `local` 边（通过 local append 写入 `edges`），并产生大量 `local_*` 指标与 notes

**依赖（总）**  
`classic_local_fill.py`、`local_reroute.py`、块提取/覆盖统计、A* 路由

**重复标注（总）**  
`重复生成风险：高`（同一 `local` 类在多个子步骤都可能新增候选）  
`几何替换：存在`（reroute）  
`最终去重消解：依赖后续第二次 _dedupe_and_snap`

**备注（总）**  
这是重复与替换最复杂的阶段。

#### 3.12.1 子步骤：classic_sprawl 主生成（若启用）
**位置**  
配置与输出类型：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/classic_local_fill.py:27`，`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/classic_local_fill.py:91`，`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/classic_local_fill.py:390`

**关键变量名**  
`local_backend`, `LocalClassicFillConfig`, `generate_classic_local_fill`, `local_traces`, `local_cul_flags`, `local_trace_meta`, `pending_local_entries`

**输入**  
`local_blocks`, 当前 `nodes/edges`（含已有 arterial/collector/branch-local），local classic 配置

**算法/策略**  
- `TerrainProbe` 驱动地形感知方向调整
- 基于块内种子（含从主干路门户 seed）进行队列式 trace 生长
- 跟踪 `LocalTraceMeta`（如 `is_spine_candidate`, `connected_to_collector`, `trace_lineage_id`, `branch_role` 等）
- 将 trace 暂存到 `pending_local_entries`，暂不立即写 `edges`
- 若数量不足，会触发补充生成需求与预算

**输出**  
`pending_local_entries` 初始 local 候选；`local_classic_*` 指标；`flags`（如 `culdesac`, `local_spine`）

**依赖**  
`classic_local_fill.py`, `TerrainProbe`, 当前主干+已有local线段上下文

**重复标注**  
`重复生成风险：高`  
原因：后续 frontier/grid supplement 还会继续向同一 `pending_local_entries` 池追加 local 候选。

**备注**  
这一步是 Local 的主要拓扑来源之一，但不是唯一来源。

#### 3.12.2 子步骤：覆盖率预评估
**位置**  
在 `_generate_hierarchy_linework` 内（Local coverage 预评估逻辑，函数起点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:1599`）

**关键变量名**  
`local_coverage_stats_pre`, `local_coverage_ratio_threshold`, `local_coverage_uncovered_polys`, `local_need_coverage_supplement`

**输入**  
`local_blocks`, 当前 `edges`, `pending_local_entries`, 河流缓冲与 coverage 半径参数

**算法/策略**  
- 计算 buildable 区域的 local 覆盖率
- 若低于阈值（代码默认 0.92），标记覆盖补充需求
- 推导补充预算（`local_grid_supplement_budget`）

**输出**  
覆盖统计、未覆盖多边形集合、补充需求标志与预算

**依赖**  
覆盖统计辅助函数、块几何

**重复标注**  
无新增道路（仅评估）

**备注**  
直接决定后续 frontier/grid supplement 是否执行。

#### 3.12.3 子步骤：frontier supplement（覆盖补洞）
**位置**  
在 `_generate_hierarchy_linework` Local 子流程内（函数起点同上）

**关键变量名**  
`frontier_cfg`, `cov_traces`, `cov_trace_meta`, `frontier_supplement_added`, `local_coverage_supplement_added_count`

**输入**  
`local_coverage_uncovered_polys`, local classic 参数（收紧后的 frontier 配置）, 当前 `pending_local_entries`

**算法/策略**  
- 用更保守/覆盖优先的 `LocalClassicFillConfig` 再跑一次 local classic fill
- 输入不再是全体 local blocks，而是“未覆盖区域多边形”
- 生成的 trace 仍先进入 `pending_local_entries`
- 再次计算覆盖率，如果已满足阈值可关闭后续覆盖补充需求

**输出**  
补洞用 local 候选，标记 `local_coverage_supplement` flag

**依赖**  
`generate_classic_local_fill`（复用），覆盖统计

**重复标注**  
`重复生成风险：高`  
原因：与主 classic_sprawl 使用相似生成器，可能在边界区域与已有 local 候选重叠或产生近似平行段。

**备注**  
这是“同算法复用但不同输入区域”的补充阶段。

#### 3.12.4 子步骤：grid supplement（密度/覆盖补充）
**位置**  
在 `_generate_hierarchy_linework` Local 子流程内（函数起点同上）

**关键变量名**  
`supplement_source_polys`, `supplement_using_coverage_polys`, `grid_supplement_added`, `local_grid_supplement_budget`, `pending_local_entries`

**输入**  
`local_blocks` 或 `local_coverage_uncovered_polys`，`local_spacing_m`, `local_jitter`, 风格参数，预算标志

**算法/策略**  
- 在 polygon 内生成平行线（`_parallel_lines_in_polygon`）
- 方向优先取“最近道路切线方向”，否则块主轴方向
- 依据预算与场景（覆盖优先/密度补充）限制条数
- 每条线转为 local 候选条目（`is_grid_supplement=True`），先放入 `pending_local_entries`
- 对 classic_sprawl underfill 场景使用更稀疏补充；覆盖场景允许更密

**输出**  
`local_grid_supplement` 候选与补充统计

**依赖**  
块几何、现有道路几何、平行线生成与裁剪

**重复标注**  
`重复生成风险：高`  
原因：和 classic/frontier 都可能在同块内生成相近线段；端点与几何均可能冲突，靠后续 reroute/intersections/dedupe 处理。

**备注**  
`local_backend!="classic_sprawl"` 时，grid_clip 也是 local 的主生成而非补充。

#### 3.12.5 子步骤：local reroute（几何重路由，替换不新增）
**位置**  
调用方在 `_generate_hierarchy_linework`（函数起点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:1599`）  
实现：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/local_reroute.py:182`  
候选选择：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/local_reroute.py:253`

**关键变量名**  
`LocalRerouteConfig`, `candidate_idxs`, `reroute_candidate_count`, `reroute_applied`, `reroute_fallback`, `entry["pts"]`, `entry["length_m"]`, `entry["flags"]`

**输入**  
`pending_local_entries`, local reroute 配置、`local_blocks`, 地形/河流

**算法/策略**  
1. `select_local_reroute_candidates(...)` 选择候选：
- 可按 `coverage` 策略（如 `selective`, `connectors_only`）
- 按 `connected_to_collector`、`is_spine_candidate`、长度、是否 grid supplement 打分排序
2. `reroute_local_polyline(...)` 对单条候选做几何重路由：
- 采样 waypoints
- 基于 trace + block + river setback 构建 corridor
- 分段调用 A* 路由
- 简化 + Chaikin 平滑
- 保持端点不变
3. 质量拒绝规则：
- “面条化”/过度弯曲（noodle）
- 长度增益过高（gain 超限）
4. 若通过，原位替换 `entry["pts"]` / `entry["length_m"]`，并加 `local_rerouted` flag

**输出**  
更新后的 `pending_local_entries`（原位替换），`local_reroute_*` 指标与 notes

**依赖**  
`local_reroute.py`、A* 路由、块/河流几何

**重复标注**  
`几何替换（不是新增）`  
这一步主要修改已有候选的几何，不新增 `edges`。

**备注**  
只有后续 local append 才真正把候选写成道路边。

#### 3.12.6 子步骤：endpoint bridge（端点桥接新增）
**位置**  
在 `_generate_hierarchy_linework` Local 子流程内（函数起点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:1599`）

**关键变量名**  
`endpoint_pool`, `overlimit_candidates`, `endpoint_bridge_*`, `used_endpoints`, `pending_local_entries`

**输入**  
`pending_local_entries`（尤其 `is_overlimit_unconnected_candidate` 候选）、地形/河流

**算法/策略**  
- 为“过长但未良好接入”的 local trace 端点构建端点池
- 选择距离/方向兼容的目标端点
- 用 A* 在地形/河流代价场中做端点桥接
- 成功则将桥接线作为新的 local 候选追加到 `pending_local_entries`（带 `local_endpoint_bridge` flag）

**输出**  
新增 local 候选条目与 `local_endpoint_bridge_*` 指标

**依赖**  
A* 路由、端点方向评分、候选池管理

**重复标注**  
`重复生成风险：中`  
原因：桥接线可能与已有 local 候选/最终线段重合或形成相同端点连接，后续依赖交叉口清理与最终去重。

**备注**  
这是 Local 阶段中明确“新增候选”的补救步骤。

#### 3.12.7 子步骤：local append（真正写入 edges）
**位置**  
在 `_generate_hierarchy_linework` Local 子流程内（函数起点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:1599`）

**关键变量名**  
`pending_local_entries`, `_append_polyline_edge`, `cul`, `entry_flags`, `continuity_id`, `parent_continuity_id`

**输入**  
最终版 `pending_local_entries`

**算法/策略**  
- 遍历候选条目
- 统一整理 `flags`（包括 `culdesac`, supplement, reroute, endpoint bridge 等）
- 调用 `_append_polyline_edge(... road_class="local")`
- 将 local 折线真正落到 `edges`

**输出**  
正式 `local` 边写入 `edges`

**依赖**  
`_append_polyline_edge`、地形/河流成本计算

**重复标注**  
`重复生成风险：兑现点`  
前面多个子步骤累积的候选都会在此阶段正式落边；真正端点级重复最终靠后续 `_dedupe_and_snap` 清理。

**备注**  
这是 Local 阶段“候选 -> 真实道路边”的提交点。

---

### 3.13 步骤 13：Local 交叉口清理（Phase 3b）
**位置**  
调用点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3415`  
实现：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/intersections.py:518`

**关键变量名**  
`target_classes={"local"}`, `local_inter_notes`, `local_inter_numeric`

**输入**  
当前 `nodes`, `edges`（focus: local）

**算法/策略**  
同步骤 7/10 的交叉口算子流程，仅作用于 local。

**输出**  
更新后的 `nodes`, `edges`，local 交叉口指标

**依赖**  
`intersections.py`

**重复标注**  
`非重复但会改变数量/形态`

**备注**  
会切分/吸附 local，影响后续 syntax 背景图与最终去重统计。

---

### 3.14 步骤 14：统一 syntax 后处理（collector/arterial choice 中心性、剪枝/加宽）
**位置**  
调用点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3436`  
实现：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/syntax.py:149`  
中心性计算：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/syntax.py:83`

**关键变量名**  
`syntax_enable`, `choice_radius_hops`, `prune_low_choice_collectors`, `prune_quantile`, `target_classes`, `scores`, `pruned`

**输入**  
`nodes`, `edges`（包含 local 作为背景）、syntax 参数

**算法/策略**  
1. 构图时把 `arterial+collector+local` 都作为 routing background
2. 只对 `target_classes`（这里是 `arterial`,`collector`）打分和处理
3. 用 edge betweenness centrality 计算选择度分数
4. 高选择度 `collector` 增宽（视觉/等级强化）
5. 低选择度 `collector` 迭代剪枝：
- 阈值由 `prune_quantile` 分位数决定
- 限制度数（避免删关键节点连接）
- 保持连通性比例不显著下降（`_largest_component_ratio` 守护）
- 至少保留一定比例 collector

**输出**  
可能减少 `collector` 数量、改变其 `width_m`，并产出 `syntax_*` notes/numeric

**依赖**  
`networkx`（若不可用会降级）、`numpy`

**重复标注**  
`非重复但会改变数量/形态`  
可能删除边（剪枝），不是重复生成。

**备注**  
Local 不被直接剪枝/加宽，但参与背景图中心性计算。

---

### 3.15 步骤 15：最终去重与节点吸附（第二次 `_dedupe_and_snap`）
**位置**  
调用点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3459`  
实现：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:2747`

**关键变量名**  
`extra2`, `duplicate_edge_count`, `zero_length_edge_count`

**输入**  
经过 intersections + syntax 后的 `nodes`, `edges`

**算法/策略**  
再次执行端点吸附与端点级去重（键仍为 `sorted(u,v)+road_class`），并把本次重复/零长统计累加到总 `extra`。

**输出**  
最终前路由的去重版 `nodes`, `edges`

**依赖**  
同步骤 5

**重复标注**  
`最终去重消解（第二次）`  
这是消解 Local 多来源候选与交叉口切分后重复的关键步骤。

**备注**  
总 `duplicate_edge_count` 是第一次 + 第二次去重统计累加。

---

### 3.16 步骤 16：最终路由与成本重算（保留已有 path_points / 重算缺失几何）
**位置**  
`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:599`（`_route_all_edges`）  
折线成本：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:238`

**关键变量名**  
`edge.path_points`, `_polyline_cost`, `path_points`, `weight`, `length_m`, `river_crossings`

**输入**  
最终去重后的 `nodes`, `edges`，地形/河流、成本参数

**算法/策略**  
- 若边已有 `path_points`，优先保留几何，只用 `_polyline_cost` 重算成本与长度/过河数
- 若边没有 `path_points`，再用 A* 生成几何
- 仅处理 `arterial/collector/local`

**输出**  
最终 `edges` 几何与成本字段定稿

**依赖**  
`_polyline_cost`, `_route_polyline_for_edge`

**重复标注**  
`非重复但会改变形态/成本`（主要是成本重算；少数无几何边会补路由）

**备注**  
这是最终几何/成本收敛步骤，不是拓扑新增。

---

### 3.17 步骤 17：Local continuity 重算（连续性分组/顺序）
**位置**  
`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:2912`

**关键变量名**  
`continuity_numeric`, `groups`, `local_auto_continuity_assigned_count`

**输入**  
最终 `nodes`, `edges`

**算法/策略**  
对 local 边重建/修正连续性分组和序号（`continuity_id`, `segment_order` 等），补齐未设置项，统计连续性组数量。

**输出**  
更新 local 连续性相关字段与 `continuity_numeric`

**依赖**  
local edge 连通关系/几何顺序逻辑

**重复标注**  
无新增（仅标注重算）

**备注**  
这是语义连续性整理，不改变道路类别。

---

### 3.18 步骤 18：Street-run 聚合（语义聚合，非新增道路）
**位置**  
调用点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3489`  
模块：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/street_run.py`

**关键变量名**  
`aggregate_street_runs`, `street_runs`, `street_run_metrics_data`

**输入**  
最终 `edges`, `nodes`

**算法/策略**  
将拓扑切分后的边聚合为语义连续街段（street runs），计算街段级指标（含 spine/class metrics）。

**输出**  
street-run 指标写入 `metrics`（不是 `artifact.roads.edges` 新边）

**依赖**  
`street_run.py`

**重复标注**  
无新增（纯分析聚合）

**备注**  
这是分析层，不是道路生成层。

---

### 3.19 步骤 19：地块/parcel 阶段步行道生成（`pedestrian_paths`，在 roads 之后）
**位置**  
接入与落盘：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/generator.py:940`  
基础步行道算法：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/blocks/parcelize.py:82`  
frontage parcel 流程（复用基础步行道）：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/blocks/parcelize.py:419`

**关键变量名**  
`extract_macro_blocks`, `parcelization`, `generate_frontage_parcels`, `generate_pedestrian_paths_and_parcels`, `artifact.pedestrian_paths`

**输入**  
`artifact.roads`（已完成道路网络）、`artifact.river_areas`、parcel 配置、`PEDESTRIAN_WIDTH_M`

**算法/策略**  
1. 先按道路+河流提取 macro blocks
2. 若 `parcels.enable=True`：
- 走 `generate_frontage_parcels`
- **内部先调用** `generate_pedestrian_paths_and_parcels` 生成基础步行道与走廊
- 再做 frontage 细分/形态耦合优化
- 返回时保留基础步行道（`pedestrian_paths=list(base.pedestrian_paths)`)
3. 若 `parcels.enable=False`：
- 直接调用 `generate_pedestrian_paths_and_parcels`

基础步行道算法（`generate_pedestrian_paths_and_parcels`）：
- 跳过小块/病态超块
- 计算块主方向并旋转到局部坐标
- 按块面积决定切割线数量（1/2/3）
- 沿短边方向做内部切线，裁剪到块内
- 把切线转换为 `PedestrianPath`
- 用步行宽度缓冲成走廊，做 `block - corridor` 得到 parcels

**输出**  
`artifact.pedestrian_paths`, `artifact.blocks`, `artifact.parcels`

**依赖**  
块提取、Shapely 几何、parcel 细分算法

**重复标注**  
`重复生成风险：低（语义上与机动车路网分层）`  
步行道与 `artifact.roads.edges` 分离，不参与 `_dedupe_and_snap`。若与道路几何重叠，属于跨图层设计问题，不是本路网去重范围。

**备注**  
步行道生成明确发生在 roads 主流程之后。

---

### 3.20 步骤 20：前端 `service` / `pedestrian` 显示兼容口径说明（非引擎生成）
**位置**  
渲染宽度与样式：`/Users/shiqi/Coding/github/GIStudio/CityGen/web/src/render/cityRenderer.ts:27`，`/Users/shiqi/Coding/github/GIStudio/CityGen/web/src/render/cityRenderer.ts:250`  
流式图层可见性：`/Users/shiqi/Coding/github/GIStudio/CityGen/web/src/render/stageRenderer.ts:485`

**关键变量名**  
`roadStrokeWidthPx`, `edge.road_class`, `isStreamingRoadVisible`, `layers.localRoads`, `layers.pedestrianPaths`

**输入**  
前端收到的 `edge.road_class` 或流式 trace 元数据

**算法/策略**  
- `cityRenderer` 对 `arterial/collector/pedestrian/service/else(local)` 分配不同宽度和样式
- `stageRenderer` 将 `service` 归入 `localRoads` 图层可见性，将 `pedestrian` 归入 `pedestrianPaths`

**输出**  
仅影响显示，不写回后端产物

**依赖**  
前端渲染层

**重复标注**  
无（显示兼容逻辑）

**备注**  
`service` 在当前静态审查中属于前端兼容类，不代表后端会生成该类道路。

---

## 4. 道路类型清单（全系统口径）

### 4.1 类型总数（结论）
- 核心路网 `road_class`（引擎真实生成，`artifact.roads.edges`）：**3 类**
- 全系统道路/通道口径（含步行通道 + `service` 显示兼容类）：**5 类**
- 全系统中引擎真实生成的通道类目（含 `pedestrian_paths`）：**4 类**

### 4.2 类型表（全系统口径）

| 类型名 | 所在数据结构 | 是否引擎真实生成 | 生成阶段 | 主要特征 | 依赖 | 算法 | 输出位置 |
|---|---|---:|---|---|---|---|---|
| `arterial` | `artifact.roads.edges[].road_class` | 是 | 步骤 3（拓扑）+ 6（几何）+ 7（清理） | 主干骨架，`render_order=0`，宽度基线大 | hub 候选图、地形、河流、交叉口算子、syntax | kNN 候选图 + MST + loop enhancement + A* + intersections + syntax（仅加权/背景） | `artifact.roads.edges` |
| `collector` | `artifact.roads.edges[].road_class` | 是 | 步骤 9（生成）+ 10（清理） | 次干路，classic_turtle 或 grid_clip | 冻结前 arterial/blocks、地形、河流、classic_growth、intersections、syntax | classic turtle growth（TerrainProbe+queue traces）或块内平行线 grid_clip | `artifact.roads.edges` |
| `local` | `artifact.roads.edges[].road_class` | 是 | 步骤 4（branch）+ 步骤 12（主local及补充）+ 13（清理） | 本地路，来源多、flags/continuity 最丰富 | 地形、河流、冻结主干 blocks、classic_local_fill、local_reroute、intersections | branch expansion + classic_sprawl + frontier supplement + grid supplement + endpoint bridge + A* reroute | `artifact.roads.edges` |
| `pedestrian`（步行通道语义） | `artifact.pedestrian_paths[]`（不是 road edge） | 是 | 步骤 19 | 地块内部切分通道，宽度常量 `PEDESTRIAN_WIDTH_M` | blocks/parcelize、Shapely、道路/河流块提取 | 块主轴旋转 + 面内切线 + corridor buffer + parcel difference | `artifact.pedestrian_paths` |
| `service`（兼容显示类） | 前端 `edge.road_class` 兼容判断 | 否（静态审查未见后端生成） | 无后端生成阶段 | 显示上归入 local 图层，有单独样式/宽度 | 前端 renderer/stageRenderer | 渲染分支与图层开关判断 | 前端显示逻辑 |

补充证据：
- `artifact.roads.edges` 序列化字段：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/generator.py:556`
- `artifact.pedestrian_paths` 写入：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/generator.py:995`

---

## 5. 重复生成与去重/替换机制专项

按你要求固定分 4 类。

### A. 真正重复候选（可能新增同端点同类 edge）
**典型位置**
- 步骤 4 `branch` 生成 `local`
- 步骤 12 classic_sprawl / frontier supplement / grid supplement / endpoint bridge（都可能产出 `local` 候选）
- 步骤 9 collector classic/grid 生成阶段（块边界和裁剪造成端点重合风险）

**原因**
- 多个步骤向同一类边集合追加（特别是 `local`）
- 步骤内更关注覆盖率/连接性/形态，而不是全局唯一性
- 候选先在 `pending_local_entries` 累积，直到 local append 才落边

**消解方式**
- 交叉口算子（可能先切分/吸附）
- 最终 `_dedupe_and_snap`（第二次）按 `sorted(u,v)+road_class` 去重  
证据：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:2784`

**注意**
- 该去重规则不会消解“几何平行但端点不同”的重复。

---

### B. 几何重叠/平行重复风险（未必同端点）
**典型位置**
- local classic vs frontier supplement（同算法，不同输入多边形，但边界区域可能重叠）
- local classic/frontier vs grid supplement（密度补充用平行线，容易形成近邻平行段）
- collector grid_clip 在相邻块边界处的平行线裁剪结果

**表现**
- 视觉上像重复道路
- 端点不同导致 `_dedupe_and_snap` 无法直接消解
- 后续 `intersections` 可能部分修正（切分/吸附），但不保证完全消除平行冗余

**系统当前处理**
- classic/local fill 内部已有接触与避让逻辑（局部约束）
- reroute 会改变几何，可能缓解重叠，也可能引入不同曲线但同走廊重复
- syntax 只处理 `arterial/collector`，不会直接清理 local 的平行重复

---

### C. 几何替换（不是新增）
**典型位置**
- local reroute（步骤 12.5）

**机制**
- `select_local_reroute_candidates` 选候选 index
- `reroute_local_polyline` 返回新折线后，直接替换 `pending_local_entries[idx]["pts"]`
- 同时更新 `length_m`、`flags`（如 `local_rerouted`）
- 未通过质量门槛则 fallback，保留原线

**结论**
- 这不是重复生成道路
- 是同一候选道路的几何优化/替换

证据：
- `reroute_local_polyline`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/local_reroute.py:182`
- 候选选择：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/local_reroute.py:253`

---

### D. 流式重复输出（不是道路重复生成）
**典型位置**
- Arterial intersection cleanup 后 snapshot 重发（步骤 8）

**机制**
- `_emit_stream_polyline_snapshot(... road_classes={"arterial"})` 将现有 arterial 几何再次发给前端
- 仅用于实时预览同步，不修改 `edges`

**结论**
- 属于显示层重复输出
- 不应计入“重复生成道路”

证据：
- 发射函数：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:86`
- 调用点：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3346`

---

### 5.1 两次 `_dedupe_and_snap` 的去重键规则（明确回答）
去重键是：**`sorted(u, v) + road_class`**  
证据：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:2784`

这意味着：
- 同端点、同 `road_class` 会被视为重复并合并
- 同端点、不同 `road_class` 不会去重
- 几何高度重叠但端点不同不会去重

### 5.2 `duplicate_edge_count` 指标累计位置与含义
- 第一次去重：步骤 5 产生 `extra.duplicate_edge_count`
- 第二次去重：步骤 15 产生 `extra2.duplicate_edge_count`
- `generate_roads` 将两次结果累加成最终指标写入 `metrics`（总重复边数）

关键位置：
- `_dedupe_and_snap` 返回统计：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:2841`
- 第二次去重调用：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3459`
- 总 metrics 输出结构：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/models.py:295`

---

## 6. 输入变量索引（按 `RoadsConfig` 分组）

字段定义总入口：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/models.py:34`  
主函数签名映射：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3047`  
入口透传：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/generator.py:369`

### 6.1 全局拓扑 / 候选图 / 主干参数
字段名：
`k_neighbors`, `loop_budget`, `branch_steps`, `slope_penalty`, `river_cross_penalty`

传递阶段：
- 步骤 2 候选图构建
- 步骤 3 主干骨架选择
- 步骤 4 分支生成
- 步骤 6/16 成本与路由计算

影响算法行为：
- 候选边密度、主干环路数量、分支扩展深度
- 坡度/河流穿越成本权重

---

### 6.2 风格 / 几何控制参数（跨 collector + local）
字段名：
`style`, `collector_spacing_m`, `local_spacing_m`, `collector_jitter`, `local_jitter`, `river_setback_m`, `minor_bridge_budget`, `max_local_block_area_m2`

传递阶段：
- 步骤 9 collector backend 生成（classic/grid）
- 步骤 11 冻结主干时 local block 提取配置
- 步骤 12 local classic/grid supplement/coverage
- 步骤 19 block extraction 尺度（间接受 `collector_spacing_m` / `max_local_block_area_m2` 影响）

影响算法行为：
- 块内平行线密度、方向扰动、河流回避缓冲
- local block 粒度与覆盖目标范围

---

### 6.3 Collector classic 参数（`classic_turtle`）
字段名：
`collector_generator`, `classic_probe_step_m`, `classic_seed_spacing_m`, `classic_max_trace_len_m`, `classic_min_trace_len_m`, `classic_turn_limit_deg`, `classic_branch_prob`, `classic_continue_prob`, `classic_culdesac_prob`, `classic_max_queue_size`, `classic_max_segments`, `classic_max_arterial_distance_m`, `classic_depth_decay_power`

传递阶段：
- 步骤 9 Collector 生成（classic backend）
- backend 配置结构：`ClassicCollectorConfig`（`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/classic_growth.py:28`）

影响算法行为：
- trace 生长长度、转向、分支概率、队列规模、与 arterial 的附着距离等

---

### 6.4 地形偏好参数（collector/local classic 共用 TerrainProbe 倾向）
字段名：
`slope_straight_threshold_deg`, `slope_serpentine_threshold_deg`, `slope_hard_limit_deg`, `contour_follow_weight`, `arterial_align_weight`, `hub_seek_weight`, `river_snap_dist_m`, `river_parallel_bias_weight`, `river_avoid_weight`

传递阶段：
- 步骤 9 collector classic
- 步骤 12 local classic/frontier classic
- `TerrainProbe` 配置注入（collector/local 各自生成器）

影响算法行为：
- 地形坡度响应（直行/蛇形/硬限）
- 等高线跟随、主干对齐、hub 吸引、河流贴近/回避偏好

---

### 6.5 Local classic 参数（classic_sprawl 主生成）
字段名：
`local_generator`, `local_classic_probe_step_m`, `local_classic_seed_spacing_m`, `local_classic_max_trace_len_m`, `local_classic_min_trace_len_m`, `local_classic_turn_limit_deg`, `local_classic_branch_prob`, `local_classic_continue_prob`, `local_classic_culdesac_prob`, `local_classic_max_segments_per_block`, `local_classic_max_road_distance_m`, `local_classic_depth_decay_power`, `local_community_seed_count_per_block`, `local_community_spine_prob`, `local_arterial_setback_weight`, `local_collector_follow_weight`

传递阶段：
- 步骤 12.1 local classic_sprawl
- 步骤 12.3 frontier supplement（会构造收紧版 classic 配置复用）

影响算法行为：
- local trace 生长策略、社区型/脊线候选倾向、与 arterial/collector 的贴近或回避

---

### 6.6 Local reroute 参数（几何重路由）
字段名：
`local_geometry_mode`, `local_reroute_coverage`, `local_reroute_min_length_m`, `local_reroute_waypoint_spacing_m`, `local_reroute_max_waypoints`, `local_reroute_corridor_buffer_m`, `local_reroute_block_margin_m`, `local_reroute_slope_penalty_scale`, `local_reroute_river_penalty_scale`, `local_reroute_collector_snap_bias_m`, `local_reroute_smooth_iters`, `local_reroute_simplify_tol_m`, `local_reroute_max_edges_per_city`, `local_reroute_apply_to_grid_supplement`

传递阶段：
- 步骤 12.5 local reroute
- 配置结构：`LocalRerouteConfig`（`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/local_reroute.py:13`）

影响算法行为：
- reroute 候选覆盖范围
- waypoints 采样密度
- corridor 大小
- 地形/河流代价放大
- 平滑/简化强度
- 是否包含 grid supplement 候选

---

### 6.7 交叉口处理参数
字段名：
`intersection_snap_radius_m`, `intersection_t_junction_radius_m`, `intersection_split_tolerance_m`, `min_dangle_length_m`

传递阶段：
- 步骤 7（arterial）
- 步骤 10（collector）
- 步骤 13（local）

影响算法行为：
- 节点吸附半径
- T 口创建半径
- crossing 切分容差
- 短悬垂段清理阈值

---

### 6.8 Syntax 参数
字段名：
`syntax_enable`, `syntax_choice_radius_hops`, `syntax_prune_low_choice_collectors`, `syntax_prune_quantile`

传递阶段：
- 步骤 14 unified syntax postprocess

影响算法行为：
- 是否启用空间句法后处理
- 选择度参数（接口保留）
- collector 剪枝开关与分位数阈值

---

## 7. 输出与产物索引

### 7.1 `artifact.roads`（最终道路网络）
写入位置：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/generator.py:551` 和 `:556`

内容：
- `nodes: RoadNodeRecord[]`
- `edges: RoadEdgeRecord[]`

`RoadEdgeRecord` 关键输出字段：
- `id`, `u`, `v`, `road_class`
- `weight`, `length_m`, `river_crossings`
- `width_m`, `render_order`
- `path_points`
- `continuity_id`, `parent_continuity_id`, `segment_order`

说明：
- 内部 `flags` 不会序列化进 `RoadEdgeRecord`

---

### 7.2 `artifact.metrics`（道路相关计数与重复指标）
定义位置：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/models.py:295`

道路相关常见输出（静态可确认）：
- `road_node_count`, `road_edge_count`
- `connected`, `connectivity_ratio`
- `duplicate_edge_count`, `zero_length_edge_count`, `illegal_intersection_count`
- `road_edge_count_by_class`
- 各阶段耗时（`road_phase_*_ms`）
- hierarchy/intersections/syntax/local reroute/coverage/street-run 指标（由 `generate_roads` 汇总）

补充证据：
- `generate_roads` 汇总 metrics：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3047`（函数内部末段）

---

### 7.3 `artifact.pedestrian_paths`
写入位置：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/generator.py:995`

来源：
- `generate_frontage_parcels(...)` 或 `generate_pedestrian_paths_and_parcels(...)`
- 二者都保留/输出 `pedestrian_paths`

说明：
- 不属于 `artifact.roads.edges`
- 不参与道路主流程的 `_dedupe_and_snap`

---

### 7.4 流式事件（补充，不计入最终道路产物）
典型事件：
- `road_node_added`
- `road_edge_added`
- `road_polyline_added`
- `road_phase_start` / `road_phase_complete`

说明：
- 用于前端实时预览
- 可能存在“同一路段重发显示”（如 arterial snapshot）
- 不等于最终 artifact 的新增道路

关键实现：
- `_emit_stream_event`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:76`
- `_emit_stream_polyline_snapshot`：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:86`

---

## 8. 易混点与误解纠正（短节）

### 8.1 “Local 是否只在 Phase 3 才出现？”
不是。  
步骤 4 分支生成阶段就会写入 `road_class="local"` 的边。证据：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:839`

### 8.2 “Pedestrian 是否在 road network 里？”
不是。  
步行通道最终在 `artifact.pedestrian_paths`，不是 `artifact.roads.edges`。证据：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/generator.py:995`

### 8.3 “service 是否当前会被引擎生成？”
当前静态审查看起来不会。  
`service` 出现在前端渲染和图层判定兼容分支中（`cityRenderer`/`stageRenderer`），但未发现引擎侧明确 `road_class="service"` 写入点。证据：`/Users/shiqi/Coding/github/GIStudio/CityGen/web/src/render/cityRenderer.ts:27`，`/Users/shiqi/Coding/github/GIStudio/CityGen/web/src/render/stageRenderer.ts:485`

### 8.4 “reroute 是否新增道路？”
通常不是。  
reroute 主要对 `pending_local_entries` 做几何替换（原位更新 `pts` 和 `length_m`），真正落边发生在后面的 local append。证据：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/local_reroute.py:182`，`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:1599`

### 8.5 “两次去重是不是重复做同一件事？”
是同一机制，但不是冗余。  
第一次清理 backbone/branch 初期重复；第二次用于消解 collector/local 多阶段生成、交叉口处理和 syntax 后的重复结果。证据：`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3221`，`/Users/shiqi/Coding/github/GIStudio/CityGen/engine/roads/network.py:3459`

---

## 附：本次审查的直接结论（浓缩版）
- 系统“核心路网”真实生成 `road_class` 只有 **3 类**：`arterial`, `collector`, `local`
- `local` 在 **多个步骤** 生成（分支阶段 + Local Phase 主流程/补充/桥接），这是你最需要关注的“重复来源”
- 真正的端点级重复主要靠 `_dedupe_and_snap`（两次）消解；几何平行重叠不一定会被完全消掉
- `local reroute` 主要是**几何替换**，不是新增道路
- `arterial` 清理后存在一次**流式重发**，是预览重复输出，不是重复道路生成
- `pedestrian_paths` 是后续地块/parcel 阶段单独生成，不属于 `artifact.roads.edges`
- `service` 当前更像前端兼容显示类，而不是后端引擎生成类型

如果你下一步要做“重复道路诊断（实例级）”，自然的延伸是跑一组固定 seed，把两次 `_dedupe_and_snap` 前后的边集导出来，统计：
1. 同端点同类重复被消解数量
2. 几何重叠但端点不同的疑似平行重复数量
3. local 各来源（branch/classic/frontier/grid/bridge）在最终保留率上的贡献比例