# City Quality Manual Acceptance Checklist

## Goal

Validate the upgraded generation pipeline for:

- hierarchical roads (`arterial`, `collector`, `local`)
- more natural river geometry (smoothed / variable width)
- finer blocks and parcels
- explicit degraded fallback behavior in the web app

## Setup

- Start backend API
- Start web app
- Use the default preset first, then compare `hilly_sparse` and `river_valley`

## Scenario A: Preview/Balanced responsiveness

1. Set `Quality = preview`
2. Click `Generate`
3. Confirm a result appears within interactive latency (target 2-5s on a typical dev machine)
4. Confirm the map shows:
   - visible arterial skeleton
   - visible collector roads
   - local roads visible after zoom-in

## Scenario B: HQ quality pass

1. Set `Quality = hq`
2. Keep `Road style = mixed_organic`
3. Click `Generate`
4. Confirm:
   - river areas look non-uniform in width
   - collector/local roads create smaller urban blocks
   - parcels appear finer and more numerous than preview mode

## Scenario C: Fallback messaging

1. Temporarily disable `/api/v2/generate` or point web app to an older backend
2. Click `Generate`
3. Confirm UI shows `Mode: staged` or `Mode: fallback` (not silent success)
4. Confirm error banner explains fallback path
5. Confirm `final_artifact.metrics.degraded_mode` is `true` in exported JSON

## Scenario D: Zoom LOD behavior

1. Generate any city with local roads present
2. Zoom out fully
3. Confirm local roads are reduced/hidden enough to avoid visual clutter
4. Zoom in
5. Confirm local roads and parcels become visible without flicker or disappearing major roads

## Scenario E: Classic Sprawl Collector / Junction Quality (new)

1. Use `Road style = mixed_organic` and ensure backend default `collector_generator=classic_turtle`
2. Generate a river-adjacent city (e.g. `river_valley`)
3. Confirm collector roads near rivers trend parallel to river banks instead of cutting directly into water
4. Inspect several collector-to-arterial contacts and confirm visible T-junctions (not only visual overlaps)
5. Export JSON and confirm `metrics.notes` includes classic collector backend and space-syntax postprocess notes

## Scenario F: Classic Urban Sprawl Morphology (new)

1. Generate a city with `collector_generator=classic_turtle`
2. Zoom into slope-rich and river-adjacent zones
3. Confirm some collector segments bend/deflect along terrain instead of remaining rigid straight grid lines
4. Confirm a small number of cul-de-sacs exist (sprawl character), but most collectors still connect into the main network
5. Compare with `collector_generator=grid_clip` fallback and confirm classic mode looks less mechanically parallel

## Scenario G: Classic Local Sprawl Fill + Parcel Coupling (new)

1. Ensure `roads.local_generator=classic_sprawl`
2. Generate `river_valley` and `hilly_sparse`
3. Confirm local streets inside neighborhoods are not predominantly parallel clip lines
4. Confirm visible cul-de-sacs exist but do not dominate every block
5. Confirm parcel shapes remain valid and do not regress into large numbers of ultra-thin strips near curvy local roads

## Scenario H: Local Geometry Reroute (new)

1. Ensure `roads.local_geometry_mode=classic_sprawl_rerouted` and `roads.local_reroute_coverage=selective`
2. Generate both `river_valley` and `hilly_sparse`
3. Confirm key local streets (collector connectors / neighborhood spines / longer segments) are not mostly two-point straight edges
4. Confirm slope-area locals show some terrain-following detours instead of direct cross-slope links
5. Confirm local-to-collector transitions look smoother than direct "needle" connections
6. Export JSON and confirm:
   - `metrics.local_reroute_candidate_count > 0`
   - `metrics.local_reroute_applied_count > 0`
   - `metrics.local_two_point_edge_ratio` is lower than the pre-reroute baseline for the same seed
   - local cul-de-sac edges still preserve `-cul` in `roads.edges[].id`
