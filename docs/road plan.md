# 三阶段线性道路管道重构（吸收 Gemini 反馈）实施计划（可直接落地）

## 摘要
本计划把当前道路生成流程收敛为严格的三阶段线性管道（Arterial -> Major Local -> Minor Local），移除 Phase A 提前生成 `local` 的干扰路径，并将 Space Syntax 固化为“宽度指导后处理（无物理剪枝）”。同时统一术语为 `Major Local / Minor Local / Minor Local Run / Minor Local Continuity`，避免引入第三套抽象命名，并通过兼容层保留现有 `road_class` 与旧指标键名。

本计划不执行代码修改（Plan Mode），但已基于当前仓库源码和测试现状做了文件级核对，包含精确变更点、兼容策略和测试清单。

## 目标与成功标准
1. 默认配置下，Phase B（collector / Major Local）开始前不再存在任何 `road_class="local"` 的边。
2. `FrozenMajorNetwork.local_blocks` 仅由 `arterial + collector` 派生，用作 Phase C 的中间态 Block 视图。
3. Syntax 主流程不再执行物理删边，后处理只允许调整 `width_m`（No backtracking pruning）。
4. 术语统一为 `Major Local / Minor Local` 与 `Minor Local Run / Continuity`，同时保持 `road_class` 协议值兼容。
5. `RoadsConfig` 新增配置项与默认值在后端、前端 TS 类型、默认配置对象三处同步。
6. 旧配置（`classic_turtle`, `tensor_streamline`）和旧指标（`local_classic_trace_*`）继续兼容至少一版。

## 现状校验结论（用于修正 Gemini 反馈并指导实现）
1. 三阶段主流程与 `FrozenMajorNetwork` 已存在于 `engine/roads/network.py`，但 Phase A 仍调用 `_generate_branches()`，提前写入 `local`，破坏线性分层。
2. Syntax 计算当前实现里，`local` 已被纳入 betweenness 背景图（`bg_classes={"arterial","collector","local"}`），但该逻辑与“目标边过滤”混写在同一函数内，容易被误读，应显式拆分。
3. Syntax 当前仍保留物理剪枝路径（`syntax_pruned_count` 来自删边逻辑），与“无回头修剪”目标冲突。
4. `generator.py` 存在大量 metrics 文案与 `Metrics(...)` 显式映射，任何命名迁移都必须改三层：
   - `engine/roads/*` 原始数值产生
   - `engine/generator.py` 文案与 `Metrics(...)` 赋值
   - `engine/models.py:Metrics` 与 `web/src/types/city.ts`
5. 前端 `GenerateConfig` 与 `CityArtifact.metrics` 类型为手写 TS，新增 `RoadsConfig` 字段或新 metrics 键必须同步 `web/src/types/city.ts`，否则类型不完整。

## 范围与非目标
### 范围（本次实施）
1. `network.py` 三阶段拓扑收敛（移除早期 local branch 主路径）
2. `syntax.py` 改为 Width Guidance Only 主路径
3. `RoadsConfig` 新增字段与默认值调整
4. `classic_local_fill.py` 术语与配置透传（Minor Local Run / Continuity）
5. `generator.py` 配置归一、metrics 文案/映射双写
6. 后端/前端类型与测试同步
7. 前端文案层术语升级（不改 `road_class` 判定逻辑）

### 非目标（本次不做）
1. 不修改 `road_class` 协议值（仍为 `arterial` / `collector` / `local`）
2. 不重写 Collector/Local 具体生长算法核心行为（只做分层与术语/参数显式化）
3. 不改 parcelization 主流程结构（仅文档/注释说明中间态 Block 与最终 Block 的差异）
4. 不删除 legacy 函数体（如 `_generate_branches`、旧 syntax prune 代码），仅从主路径移除并标记 deprecated/legacy

## 重要接口/API/类型变更（决策已定）
### 后端 `RoadsConfig`（`engine/models.py`）
新增字段（默认值固定）：
1. `enable_legacy_branches: bool = False`
2. `local_minor_run_hard_cap_m: float = 6000.0`
3. `local_sub_branch_interval_min_m: float = 200.0`
4. `local_sub_branch_interval_max_m: float = 400.0`
5. `local_sub_branch_max_depth: int = 2`
6. `local_sub_branch_connector_seek_radius_m: float = 1200.0`

默认值调整：
1. `syntax_prune_low_choice_collectors: bool = False`（从 `True` 改为 `False`）

兼容别名归一：
1. `collector_generator` 接受 `turtle_flow`（推荐值）
2. `classic_turtle` 与 `tensor_streamline` 归一到 `turtle_flow`

### 后端 Syntax 接口（`engine/roads/syntax.py`）
新增主入口：
1. `apply_width_guidance_postprocess(...)`（仅宽度调整，不删边）

兼容保留入口：
1. `apply_syntax_postprocess(...)` 保留函数名，但转为 wrapper（不执行物理剪枝，输出 deprecated note）

### 指标与术语（兼容期双写）
新增指标键（与旧键双写）：
1. `minor_local_run_count`（映射自 `local_classic_trace_count`）
2. `minor_local_run_generator_enabled`（映射自 `local_classic_enabled`）
3. `minor_local_continuity_group_count`（映射自 `local_continuity_group_count`）
4. `minor_local_edges_with_continuity_count`（映射自 `local_edges_with_continuity_count`）

注：旧键保留至少一版，前端/文案逐步切换到新键。

## 术语统一（实现规范）
### 协议层（保持不变）
1. `road_class="collector"` 仍存在（不改为 `major_local`）
2. `road_class="local"` 仍存在（不改为 `minor_local`）

### 代码/文档/文案层（统一）
1. `Collector` -> `Major Local`（显示/文案）
2. `Local` -> `Minor Local`（显示/文案）
3. `Trace` -> `Minor Local Run`（生成期术语）
4. `Trace continuity` -> `Minor Local Continuity`
5. 不引入 `Semantic Run`
6. 不引入 `Radial Spine` 分类名；如需区分来源，仅在 meta 中标记 `seed_origin`

## 文件级实施方案（精确到改动类型）

### 1. `engine/roads/network.py`
目标：从主路径移除 Phase A 提前 local，并明确中间态 Block 语义

改动内容：
1. 修改 `generate_roads(...)` 签名，新增 `enable_legacy_branches` 和 Minor Local Run 调度参数透传（与 `RoadsConfig` 对齐）。
2. 在 Arterial Backbone 构建后，将 `_generate_branches(...)` 从主路径移除。
3. 保留 `_generate_branches(...)` 函数体，但仅在 `enable_legacy_branches=True` 的实验兼容路径下调用。
4. 增加 notes：
   - `legacy_phase1_branches:disabled`（默认）
   - `legacy_phase1_branches:enabled`（兼容实验）
   - `transient_major_block_view_count:<n>`（标记中间态 Block 视图）
5. 维持现有三阶段 intersections、`_freeze_major_network`、local-only、endpoint bridge、final dedupe/route/continuity/street-run 的顺序不变。
6. 将 Syntax 调用从 `apply_syntax_postprocess(...)` 切换为 `apply_width_guidance_postprocess(...)`（如保留 wrapper，可继续调用旧名但明确传入不剪枝并写 deprecated note；推荐直接切新函数名）。

关键伪代码（主路径）：
```python
# Phase A: arterial only
graph, candidate_debug = _build_candidate_graph(...)
selected_backbone = _generate_backbone_edges(graph, loop_budget)
edges += materialize_arterials(selected_backbone)

if enable_legacy_branches:
    notes.append("legacy_phase1_branches:enabled")
    _generate_branches(...)
else:
    notes.append("legacy_phase1_branches:disabled")

nodes, edges, extra = _dedupe_and_snap(nodes, edges)
_route_all_edges(...)
nodes, edges, art_inter_notes, art_inter_numeric = apply_intersection_operators(target_classes={"arterial"}, ...)
_emit_stream_polyline_snapshot(..., road_classes={"arterial"})  # preview only

# Phase B: collector / Major Local
_generate_hierarchy_linework(generation_phase="collector_only", ...)
nodes, edges = apply_intersection_operators(target_classes={"collector"}, ...)
frozen_major = _freeze_major_network(...)
notes.append(f"transient_major_block_view_count:{len(frozen_major.local_blocks)}")

# Phase C: local / Minor Local
_generate_hierarchy_linework(generation_phase="local_only", frozen_major_network=frozen_major, ...)
nodes, edges = apply_intersection_operators(target_classes={"local"}, ...)

# Final syntax width guidance only
edges, syntax_notes, syntax_numeric = apply_width_guidance_postprocess(...)
```

兼容细节：
1. `_generate_branches` 产生的 `road_phase_branches_ms` 指标在默认路径会变 0；保留字段但默认不产值。
2. `branch_steps` 配置暂保留，兼容 `enable_legacy_branches=True`；后续版本可再废弃。

### 2. `engine/roads/syntax.py`
目标：明确 “local 作为背景图” 逻辑，主路径不再物理剪枝

改动内容：
1. 拆分 `compute_space_syntax_edge_scores(...)` 内部逻辑为两个私有 helper：
   - `_build_syntax_background_graph(nodes, edges)`：明确包含 `arterial/collector/local`
   - `_collect_target_edges_for_scoring(edges, target_classes)`：只收集被打分映射的目标类（默认 arterial/collector）
2. 新增 `apply_width_guidance_postprocess(...)`：
   - 读取 syntax 分数
   - 对高 choice collector 加宽
   - 可选对低 choice collector 收窄到下限，但不删边
   - 明确返回 `syntax_pruned_count = 0`
3. 将 `apply_syntax_postprocess(...)` 变为兼容 wrapper：
   - 添加 note：`syntax:deprecated_apply_syntax_postprocess`
   - 忽略 `prune_low_choice_collectors` 的物理剪枝含义
   - 调用 `apply_width_guidance_postprocess(...)`
   - 如 `prune_low_choice_collectors=True`，增加 note：`syntax:prune_deprecated_ignored`
4. 保留旧剪枝实现代码作为 private legacy helper（不在主路径使用），以便后续比较或紧急回滚。

关键伪代码（结构）：
```python
def _build_syntax_background_graph(nodes, edges):
    # include arterial + collector + local

def _collect_target_edges_for_scoring(edges, target_classes):
    # default targets: arterial + collector

def compute_space_syntax_edge_scores(...):
    syntax_edges = _collect_target_edges_for_scoring(...)
    g = _build_syntax_background_graph(...)
    pair_scores = nx.edge_betweenness_centrality(...)
    return edge_scores, notes

def apply_width_guidance_postprocess(...):
    scores, notes = compute_space_syntax_edge_scores(...)
    out = rebuild_collector_widths_only(edges, scores)
    numeric["syntax_pruned_count"] = 0.0
    notes.append("syntax:width_guidance_only")
    return out, notes, numeric

def apply_syntax_postprocess(...):
    notes = ["syntax:deprecated_apply_syntax_postprocess"]
    if prune_low_choice_collectors:
        notes.append("syntax:prune_deprecated_ignored")
    return apply_width_guidance_postprocess(...)
```

约束（必须满足）：
1. 后处理前后 `len(edges)` 相同
2. 后处理前后 `edge.id` 集合相同
3. 后处理只改 `width_m`（及由 rebuild 派生的对象拷贝）

### 3. `engine/models.py`
目标：配置默认值、兼容 alias、Metrics 字段扩展

改动内容（`RoadsConfig`）：
1. 新增字段：
   - `enable_legacy_branches`
   - `local_minor_run_hard_cap_m`
   - `local_sub_branch_interval_min_m`
   - `local_sub_branch_interval_max_m`
   - `local_sub_branch_max_depth`
   - `local_sub_branch_connector_seek_radius_m`
2. 默认值调整：
   - `syntax_prune_low_choice_collectors = False`
3. 在 `model_validator(mode="before")` 中扩展别名归一：
   - `collector_generator in {"classic_turtle","tensor_streamline"}` -> `"turtle_flow"`

改动内容（`Metrics` 模型）：
1. 新增可选字段：
   - `minor_local_run_count`
   - `minor_local_run_generator_enabled`
   - `minor_local_continuity_group_count`
   - `minor_local_edges_with_continuity_count`
2. 旧字段不删（兼容）

实现约束：
1. `StrictModel(extra="forbid")` 不变
2. 新字段都必须有合理类型与默认（`Optional[...] = None` 或有默认值）

### 4. `engine/roads/classic_local_fill.py`
目标：Minor Local Run/Continuity 术语显式化，配置透传，保留兼容返回签名

改动内容：
1. `LocalClassicFillConfig` 增加新字段（与 `RoadsConfig` 对齐）：
   - `local_minor_run_hard_cap_m`
   - `local_sub_branch_interval_min_m`
   - `local_sub_branch_interval_max_m`
   - `local_sub_branch_max_depth`
   - `local_sub_branch_connector_seek_radius_m`
2. `LocalTraceMeta`（兼容期保留类名）新增字段：
   - `minor_local_continuity_id`
   - `parent_minor_local_continuity_id`
   - `seed_origin`（如 `major_portal_seed`, `hub_seed`, `sub_local_scheduler`, `coverage_frontier`, `grid_supplement`, `endpoint_bridge`）
3. 在生成过程中将现有 `trace_lineage_id` / `parent_trace_lineage_id` 与新 continuity 字段双写。
4. 用新配置字段驱动现有 hard cap 和 sub-branch cadence，不改变核心生成算法结构。
5. notes/numeric 增加 Minor Local Run 命名的双写键；旧 `local_classic_trace_*` 继续保留。
6. 不引入 `Semantic Run`，不引入 `Radial Spine` 类型。

关键伪代码（概念双写）：
```python
meta.trace_lineage_id = continuity_id                 # legacy
meta.parent_trace_lineage_id = parent_continuity_id   # legacy
meta.minor_local_continuity_id = continuity_id        # new
meta.parent_minor_local_continuity_id = parent_continuity_id
meta.seed_origin = seed_origin
meta.branch_role = "mainline" | "sub_local"
```

兼容约束：
1. `generate_classic_local_fill(...)` 返回签名不改（`traces, cul_flags, trace_meta, notes, numeric`）
2. 旧测试若检查 `local_classic_trace_*` 仍应通过（并新增新键断言）

### 5. `engine/generator.py`
目标：新增配置透传、collector generator 归一、metrics notes/typed Metrics 同步

改动内容：
1. 更新 `collector_generator` 归一逻辑：
   - 输入 `turtle_flow`（推荐）
   - 兼容 `classic_turtle` / `tensor_streamline`
   - 对 `generate_roads(...)` 的实际 backend 值做统一映射（建议在 `network.py` 中仍映射到现有 backend 名）
2. 将新增 `RoadsConfig` 字段透传至 `generate_roads(...)`
3. 更新 metrics 文案层（`metric_notes`）：
   - 使用 `Major Local` / `Minor Local Run` 术语
   - Syntax 文案改为 width guidance 语义，避免 “pruned=...”
4. 在 `Metrics(...)` 构造时新增新指标字段赋值（双写）
5. 保留旧 metrics 文案一版兼容（或至少不删除旧 key 的读取）

关键改动点（已确认现有位置）：
1. `collector_generator_value` 归一：`engine/generator.py` 中 `roads_cfg` -> `generate_roads(...)` 透传段
2. `metric_notes` 生成段（当前大量使用 `local_classic_trace_*` 和 `syntax_pruned_count`）
3. `Metrics(...)` 构造段（显式字段赋值）

### 6. `web/src/types/city.ts`
目标：同步前端 TS 类型（GenerateConfig 与 CityArtifact.metrics）

改动内容（`GenerateConfig.roads`）：
1. 新增可选字段：
   - `enable_legacy_branches?: boolean`
   - `local_minor_run_hard_cap_m?: number`
   - `local_sub_branch_interval_min_m?: number`
   - `local_sub_branch_interval_max_m?: number`
   - `local_sub_branch_max_depth?: number`
   - `local_sub_branch_connector_seek_radius_m?: number`
2. `collector_generator?: string` 保持，文档/默认值改为 `turtle_flow`

改动内容（`CityArtifact.metrics`）：
1. 新增可选 metrics 字段：
   - `minor_local_run_count?: number`
   - `minor_local_run_generator_enabled?: number`
   - `minor_local_continuity_group_count?: number`
   - `minor_local_edges_with_continuity_count?: number`
2. 保留现有 `local_*` 旧字段（兼容）
3. `syntax_pruned_count` 若前端直接展示可继续保留（默认将长期为 0）

### 7. `web/src/App.tsx`
目标：同步默认配置对象，确保新增字段在前端可控并与后端默认对齐

改动内容：
1. `defaultConfig.roads` 新增字段默认值：
   - `enable_legacy_branches: false`
   - `local_minor_run_hard_cap_m: 6000`
   - `local_sub_branch_interval_min_m: 200`
   - `local_sub_branch_interval_max_m: 400`
   - `local_sub_branch_max_depth: 2`
   - `local_sub_branch_connector_seek_radius_m: 1200`
   - `collector_generator: "turtle_flow"`（若需要显式默认）
2. `syntax_prune_low_choice_collectors` 若前端配置对象里有显式值，改为 `false`

### 8. 前端文案文件（UI/Timeline/Inspector）
目标：用户侧术语统一，不改协议判断

改动内容：
1. 将以下文案替换：
   - `"Collector Roads"` -> `"Major Local Roads"` 或 `"Major Local Network"`（确定一个，统一全站）
   - `"Local Roads"` -> `"Minor Local Roads"` 或 `"Minor Local Network"`（确定一个，统一全站）
   - `"Growing collector network from arterial seeds"` -> `"Growing major local network from arterial seeds"`
   - `"Filling blocks with local street network"` -> `"Filling blocks with minor local network"`
2. 保持渲染逻辑判断不变：
   - `edge.road_class === 'collector'`
   - `edge.road_class === 'local'`

受影响文件（已确认命中）：
1. `web/src/timeline/unifiedStages.ts`
2. `web/src/ui/StageInspector.test.tsx`
3. `web/src/ui/Controls.test.tsx`（若标签文本变更）
4. 可能还包括 `web/src/ui/Controls.tsx` 标签与图例文案

## 数据流与依赖关系（决策已定）
1. Phase A 只产生 `arterial` 拓扑与几何。
2. Phase B 读取 Phase A 输出，生成 `collector`（Major Local），并做 collector intersections。
3. Phase B 完成后通过 `_freeze_major_network(...)` 得到中间态 `local_blocks`（瞬时 Block 视图）。
4. Phase C 读取 `frozen_major_network.local_blocks`，生成 `local`（Minor Local）主/补充/桥接，并做 local intersections。
5. Final 阶段执行宽度指导（Syntax），不改变拓扑，只调 `width_m`。
6. 所有道路定稿后，再在 `generator.py` 的 land-use 层做最终 block extraction + parcelization。

## 兼容策略（避免一次性破坏）
1. `_generate_branches(...)` 保留函数实现，但默认不走主路径。
2. `apply_syntax_postprocess(...)` 保留函数名，内部转调宽度指导主函数，并输出 deprecated note。
3. `collector_generator` 旧值 `classic_turtle` / `tensor_streamline` 仍可接受，归一到 `turtle_flow`。
4. 旧 metrics 键（`local_classic_trace_*` 等）保留，新增 `minor_local_*` 双写键。
5. `road_class` 协议值不变，前端渲染逻辑无需跟随重命名，仅改文案。

## 实施步骤（建议按 6 个提交拆分，便于回滚）
### 提交 1：三阶段主路径收敛（后端核心）
1. 修改 `engine/roads/network.py`
2. 移除 `_generate_branches(...)` 主路径调用
3. 新增 `enable_legacy_branches` 参数与 notes
4. 保持 tests 先不改文案/metrics（最小闭环）
5. 验证：Phase B 前不生成 `local`

### 提交 2：Syntax 宽度指导化（无回头修剪）
1. 修改 `engine/roads/syntax.py`
2. 拆出背景图 helper 和目标边 helper（显式包含 local 背景图）
3. 新增 `apply_width_guidance_postprocess(...)`
4. `apply_syntax_postprocess(...)` wrapper 化、忽略 prune
5. 修改 `engine/roads/network.py` 调用新函数（或继续走 wrapper 并记录 note）
6. 验证：后处理前后边数量/ID 不变

### 提交 3：配置默认值与 alias 归一
1. 修改 `engine/models.py:RoadsConfig`
2. 修改 `engine/generator.py` 归一与透传
3. 修改 `web/src/types/city.ts` 与 `web/src/App.tsx` 默认配置同步
4. 验证：旧配置 payload 仍可通过 API（`classic_turtle`, `tensor_streamline`）

### 提交 4：Minor Local Run / Continuity 术语显式化（后端）
1. 修改 `engine/roads/classic_local_fill.py`（配置字段、meta 字段、notes/numeric 双写）
2. 修改 `engine/roads/network.py`（透传新配置、双写指标汇总）
3. 修改 `engine/generator.py`（metrics notes 与 `Metrics(...)` 映射）
4. 修改 `engine/models.py:Metrics` 增新字段
5. 验证：旧 tests 继续通过，新 keys 出现

### 提交 5：前端 metrics/类型与文案层统一
1. 更新 `web/src/types/city.ts` metrics 可选字段
2. 更新 UI 文案与 timeline 文案（Major/Minor Local）
3. 更新相关 UI tests 的文本断言
4. 保持渲染逻辑和 layer toggles 不变

### 提交 6：补充测试与回归固化
1. 新增 syntax 背景图显式测试
2. 新增 no-pruning 拓扑冻结测试
3. 新增 Phase B 前无 local 的生成流程测试（可通过 notes/metrics 或内部 helper 测试）
4. 增强 local run/continuity 双写指标测试

## 测试用例与验收场景（必须覆盖）
### A. 后端生成流程（三阶段线性）
1. 默认配置生成一次城市，检查最终结果成功。
2. 验证无异常 metrics 丢失（`duplicate_edge_count`, `road_edge_count_by_class`, `notes` 等仍存在）。
3. 新增/修改测试：断言 Phase B Collector 生成输入不含 `local`
   - 推荐通过 instrumentation note 或拆分 helper 单测，不建议依赖 fragile 日志文本。

### B. Syntax（无回头修剪）
1. `apply_width_guidance_postprocess` 前后 `len(edges)` 相同。
2. `apply_width_guidance_postprocess` 前后 `set(edge.id)` 相同。
3. `syntax_pruned_count == 0`。
4. 高分 collector 宽度可增大（至少一个测试图触发）。
5. 新增显式测试：background graph 包含 local，target scoring 不对 local 直接出分。

### C. 兼容配置
1. API 请求 `collector_generator="classic_turtle"` 成功。
2. API 请求 `collector_generator="tensor_streamline"` 成功并归一。
3. API 请求 `collector_generator="turtle_flow"` 成功。
4. 旧 payload 未包含新 `RoadsConfig` 字段时仍使用默认值。

### D. Minor Local Run/Continuity 命名双写
1. `generate_classic_local_fill` notes/numeric 同时含旧键和新键（至少核心计数键）。
2. `generator.py` 输出 `Metrics` 中同时有旧/新字段（新字段可选但应存在于有数据场景）。
3. 前端 TS `CityArtifact.metrics` 类型允许读取新字段，不报类型错。

### E. 前端文案与渲染兼容
1. UI 文案显示 `Major Local` / `Minor Local`。
2. 旧渲染逻辑仍按 `collector` / `local` 路由着色和图层筛选。
3. Stage inspector / timeline 测试更新后通过。

## 风险与缓解（实施时按此执行）
1. 风险：移除 Phase A `_generate_branches` 后城市边缘稀疏  
   缓解：保留 `enable_legacy_branches` 实验开关；先观察 Phase C local generator + endpoint bridge 的补足效果。
2. 风险：Syntax 不删边导致 collector 密度偏高  
   缓解：通过上游 collector spacing/jitter/classic_turtle 参数调结构；Syntax 仅做宽度表达。
3. 风险：术语迁移波及范围过大（tests/UI/metrics）  
   缓解：双写指标 + 文案层先行 + 协议值不变。
4. 风险：Gemini 类似误读再次发生（local 背景图逻辑）  
   缓解：在 `syntax.py` 显式拆分 helper 并新增测试，逻辑结构自证。

## 显式假设与默认值（本计划采用）
1. 默认不允许 Phase A 生成任何 `local`，因此 `_generate_branches` 主路径禁用。
2. `collector`/`local` 协议值短期不变；`Major Local`/`Minor Local` 仅为显示/文案/开发术语层。
3. Syntax 物理剪枝在主路径上完全停止；兼容 wrapper 只做 width guidance。
4. 指标迁移采用双写至少一版，不做一次性破坏性重命名。
5. 前端配置类型与默认值会同步新增字段，但 UI 面板不必一次性暴露所有新参数（可先类型/默认值支持）。

