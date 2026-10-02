# Goal: a world build that scales

Paste the block below as the goal of an agent run (for example after `/goal`). Written 2026-10-01 after the data-inventory work
was merged (master `bd66c736`). Measured then: `tools/data/big/hg` 19 GB and `vec` 4 GB for the Haute-Garonne region
(about 0.6 MB/km² of sources), `tools/data/big/roads` 22 GB of road pipeline caches, the small map's world 33 -> 92 MB with the
ground fill, about 3 min to build the small map (9 sectors), `world_berat70new` 2.0 GB.

```text
Make the world build scale: berat70new rebuilt in 10 minutes or less, output and intermediates in 20 GB or less, sources in 1 MB/km² or less, and every stage able to grow to the whole of France with bounded RAM.

Read first: docs/data-inventory.md (final report and fixes), docs/roads.md, docs/data-sources.md, tools/build_world.py, tools/roads/build.py, tools/stitch.py, tools/fetch_hg.py, tools/fetch_ground.py, tools/roads/osm.py.

Targets (berat70new = data/big/berat70new_sectors.json, 484 sectors / ~4,950 km², on this Mac: M1 Max, 10 cores, 32 GB):
- Time: 10 min wall clock or less for a full rebuild from cached sources (roads build + world build + far terrain + indexes).
- Disk, output + intermediates: 20 GB or less for berat70new (today: tools/data/big/roads alone is 22 GB, the small map's world grew 33 -> 92 MB with the ground fill). Aim for an output of about 1 MB/km² or less.
- Disk, sources: 1 MB/km² or less (LiDAR mnt/mnh, ortho, vectors, OSM), stored per tile in the leanest lossless or visually lossless form; today about 0.6 MB/km² (hg 19 GB + vec 4 GB for the region).
- RAM: a peak per worker that does not grow with the map (state it, e.g. 2 GB). Prove it: the same peak on 9, 100 and 484 sectors.
- Quality: no regression on the small map against master bd66c736 (check_roads, check_gaps, check_buildings, check_ground, roads build report, playtest -roadtest / -autotest, tools/shot_compare.py at the docs/shots/datainv spots).
- Report the per-km² model and its extrapolation to France (~550,000 km²): build time, disk (sources, output), RAM.

Sources (fetched per tile, on demand):
- A build asks for the tiles it needs; anything missing is fetched for those tiles only, resumable, in parallel within each service's limits, and cached per tile. No whole-area download, no whole-area file.
- OSM: the massif-extractor database on mace (ssh mace, read-only transaction, bbox-bounded per tile) for what it holds (highways, points, waterways, natural, places, a filtered import without landuse or buildings); Overpass per tile for the rest (landuse, car parks, building tags, turn restrictions), politely rate-limited.
- IGN: WFS vectors and WMS / LiDAR HD rasters per tile; RPG, BD Haie and the orthophoto row measurements per tile (the latter only for large row-crop parcels, cached).
- Measure the first-time fetch of berat70new (time, volume, requests per service) and report it; it is not part of the 10 min.

Rules:
- Branch off master, commit each step; never push master and never release (I merge).
- Python via uv only. Measure, never guess: profile before optimising and keep the numbers in the report.
- Chunks only, never the whole surface: no stage may load, rasterise, union or pickle the whole map. Every stage works per tile (sector or chunk) plus a bounded halo, and borders must come out identical from both sides (deterministic inputs, shared seam geometry). The whole-map Network pickle, the far-terrain npz cache, world-wide vector unions and any other global array must become tiled.
- Incremental: a tile is rebuilt only when its inputs (or the code of its stage) changed: content hashes per tile, no timestamps.
- A chunk-format change is BM08: keep BM07 and BN02 readable, and flag that online players need the new map. Smaller files are welcome (quantised heights, indexed vertices, byte weights, compression) if check_gaps and the screenshots stay identical.
- If a step turns out much bigger than planned, stop and report.

Steps (verify each before the next):
1. Measure: wall time, CPU time, peak RSS and disk per stage and per sector, for the small map and for 20 berat70new sectors; the per-km² cost model and its extrapolation; the list of global (non-tiled) steps. Commit the profiling tool.
2. Source tiles: one per-tile cache layout for every source (rasters, vectors, OSM from mace and Overpass, ground layers, row measurements) with a fetch-on-demand driver; convert today's caches without re-downloading.
3. Roads pipeline per tile: links, junctions, profile QP, lanes and lane graph per sector with a halo, borders stitched deterministically; no whole-network state. Same output as today on the small map.
4. World build hot spots: the ground fill (tools/stitch.py, ~4 M triangles on the small map: vectorise it, coarser away from the edge), the paved-height and field lookups, buildings and roofs, ground classes; share work between neighbouring sectors' halos where possible.
5. Far terrain, places, world.json, spawn: tiled and incremental, no whole-map arrays.
6. Disk: intermediates dropped or compressed, the output world under budget (BM08 if needed).
7. Full run: berat70new built from scratch (cached sources) within the targets; a playable world written next to world_berat70new (not over it), check_roads / check_gaps / playtest on it, shots at a few spots outside the small map.

Done when berat70new rebuilds in 10 min or less within the disk and RAM targets, the small map has no regression, and docs/build-scaling.md gives per-stage before / after numbers, the per-km² model, the France projection (time, disk, RAM) and what is left.
```
