# A world build that scales

Goal (docs/build-scaling-goal.md): berat70new (484 sectors, ~4,950 km²) rebuilt from cached sources in 10 minutes or less, output
and intermediates in 20 GB or less, sources in 1 MB/km² or less, and every stage able to grow to the whole of France with a bounded
RAM per worker. Machine: M1 Max, 10 cores (one is taken by a VM running all day: 9 usable), 32 GB.

Measuring: `tools/profile_build.py` runs a build command, samples the whole process tree (resident memory per process and in
total, CPU time), echoes every output line with its time and keeps the record in `data/big/profile/<name>.json`. The world builder
now reports per sector its CPU time, the peak resident memory of its worker and the wall time of each part (`parts`).

## Step 1: where the time, the memory and the disk go (master bd66c736)

Two areas: the small map (9 sectors, rural) and `b70s20`, a 5 x 4 block of berat70new (si 7..11, sj 3..6) chosen because its
density of buildings and roads (BD TOPO file sizes) is the mean of the 484 sectors. Builds from scratch (`--fresh`), 9 workers.

| | small: roads | small: world | b70s20: roads | b70s20: world |
|---|---|---|---|---|
| wall | 30.7 s | 209 s | 77 s | 653 s |
| CPU (whole tree) | 54 s | 830 s | 155 s | 3,160 s |
| CPU per sector | 6.0 s | 92 s | 7.8 s | 158 s (41 - 590) |
| peak resident, one process | 584 MB (main) | 1,758 MB (a worker) | 1,000 MB (main) | 2,688 MB (a worker) |
| disk written | 87 MB sector files + 67 MB network + 22 MB cache | 90 MB | 246 + 187 + 57 MB | 242 MB |

World build, b70s20, mean wall time per sector and part (9 sectors in parallel):

| part | s / sector | share |
|---|---|---|
| stitch_fill (the ground around the paved surfaces, `stitch.fill`) | 154.4 | 83.6 % |
| ground_areas (land use polygons, car parks) | 8.8 | 4.8 % |
| buildings (footprints cut from the roads, roofs) | 8.0 | 4.3 % |
| field (the paved-edge height field, cut cells) | 6.1 | 3.3 % |
| write, ground, far_mesh, facades, terrain, colour, trees, ... | 7.3 | 4.0 % |

Inside `stitch.fill` (cProfile, one sector): 23 s of 50 in `PavedHeight.__call__` (a Python loop per point over the triangles
near it), 9 s in `dwithin` against the window-wide paved outline (not prepared), 5 s of box differences against it, ~8 s
iterating triangles one by one in Python.

Road build, b70s20, fresh: source 3.3 s, graph 0.4, alignment 6.9, junction 5.6, crossing 0.7, profile 20.4 (the only tiled
stage), lanes 26.4, lanegraph 8.6, report + save ~5 s. Everything but the profile runs in one process over the whole area.

### Per-km² model (baseline)

| | per km² | berat70new (4,956 km²) | France (550,000 km²) |
|---|---|---|---|
| world build CPU | 15.4 s | 21 h CPU, 2 h 20 min on 9 cores | 2,350 h CPU, ~11 days on 9 cores |
| road build CPU | 0.76 s (but ~70 % in one process) | 1 h CPU, ~30 min wall (measured: 89 min for berat70 before build-speed) | not feasible in one process |
| world output | 1.18 MB | 5.9 GB | 650 GB |
| road intermediates (sector pickles, network pickle, caches) | 2.4 MB | 11.9 GB (on disk today: 7.7 GB for berat70new) | 1.3 TB |
| sources (LiDAR mnt / mnh, ortho, BD TOPO, OSM) | 0.51 MB | 2.5 GB | 280 GB |
| RAM | worker 1.8 - 2.7 GB; road main process grows with the area (2.5 GB network pickle for berat70new) | | |

CPU per sector follows the road surface: a least-squares fit gives `cpu ≈ 0.50 s x road pieces - 79 s` on b70s20 (the fill
works along every paved edge); buildings hardly count.

Sources per km² (whole region on disk, 4,837 sectors): LiDAR mnt 0.253 MB (uint16 5 cm, npz), mnh 0.149 MB (uint16 0.1 m, npz),
ortho 0.011 MB (JPEG 4 m), BD TOPO 0.085 MB (gzip GeoJSON), ground layers ~0.03 MB, OSM ~0.004 MB. Total 0.51 MB/km²: already
under the 1 MB/km² target; what is missing is the layout (step 2).

First-time fetch of the b70s20 sources not on disk (42 sectors of window): OSM roads + junction control (mace, Overpass, WFS) 4.3 s;
OSM roof tags (Overpass) 19 s; ground layers (5 WFS layers x 42 sectors, Overpass land use, 3,539 orthophoto row measurements) 5 min 16 s.

### Global (non-tiled) steps

Road pipeline (`tools/roads`), all in one process over the whole area unless said:
- `source.load_edges` reads every BD TOPO road file of the area; `osm.load` / `controls.load` read one OSM file per area
  (`osm/roads_<tag>.json.gz`, `controls_<tag>.json.gz`) and match against an STRtree of all ways.
- `graph.build`, `strokes`, `links`: the whole network; `alignment` smooths every stroke in one process, its cache
  (`<tag>.cache/alignment.pkl`, 651 MB for berat70new) is one pickle.
- junctions (forked workers but the whole network in memory), crossing, lanes, lanegraph, the report: whole network.
- profile: tiled (3.2 km tiles, checkerboard rounds), but the network is in memory and the solve runs inside the same process tree.
- `save`: the whole `Network` pickle (2.5 GB for berat70new) and per-sector pickles cut from the whole lists.

World build (`tools/build_world.py`):
- per process, whole-area files: `osm_ground(tag)` (OSM land use of the area), `row_directions(tag)`, `roofs.osm_buildings(tag)`
  (and a linear scan of it per building), `load_pois()` (every POI file of the region), `places.json` (copied whole).
- the far terrain: `far.bin` and its cache (`far_<tag>.npz`) are whole-map arrays, rewritten on every run.
- `world.json`, `spawn.json`: small, written whole.

Fetchers: one file per area for OSM roads, junction control, OSM land use, OSM roof tags, orthophoto row measurements; POIs per
departement; rasters and BD TOPO per tile already.

## Step 2: source tiles, fetched on demand (`tools/sources.py`)

One layout for every source, one file per tile and kind under `data/big/src/<kind>/`: the LiDAR rasters per 1.6 km tile, the
orthophoto per 3.2 km tile, and everything else per sector tile (3.2 km): BD TOPO layers (roads, buildings, water, vegetation,
transport, structures, `non_communication`), RPG, BD Haie, OSM from the massif-extractor database on mace (drivable ways, control
nodes) and from Overpass (land use, car parks, roof tags, turn restrictions, points of interest, place names: one query per tile,
the five answers separated by `out count`), and the orthophoto row measurements of the rowed parcels. A line or area is kept in
every tile its box meets (readers de-duplicate on its id), a point in the tile holding it.

A build asks for the tiles of its sectors and their neighbours (`sources.ensure`, called by `python -m roads build` and
`build_world.py`); whatever is missing is fetched for those tiles only, written atomically (re-running resumes), each service with
its own pool (IGN WMS 4, WFS 3, mace 1 ssh session per 40 tiles, Overpass 1 query at a time 2 s apart, orthophoto crops 6). The
fetch report (requests, MB and seconds per service) goes to `data/big/profile/fetch_<tag>.json`. `uv run python sources.py status
<list>` says what a list has and lacks.

Rasters are stored lossless and leaner: the uint16 codes (5 cm ground, 0.1 m canopy) as the difference along each row, zstd level
19 (decoded in 2.5 ms a tile; xz was 31 ms); the float32 tiles of the original 10 x 10 block with their bytes shuffled, zstd.
The keys of the incremental builds now hash the content of every tile a sector or a height tile read (`sources.stamp`), not
file names and times.

Conversion of today's caches (`convert_sources.py`, once, nothing downloaded): rasters re-encoded and checked bit for bit before
the old file was removed; BD TOPO files moved (the Haute-Garonne ones gzipped); the per-area OSM files split into the tiles they
cover wholly (a tile only partly covered is left to be fetched); POIs and place names split by tile.

| source | before | after |
|---|---|---|
| LiDAR ground `mnt` (region, 19,348 tiles) | 12.3 GB npz + 1.0 GB float32 .npy (original block) | 5.97 GB |
| LiDAR height `mnh` | 7.2 GB npz + 1.0 GB .npy | 6.39 GB |
| orthophoto | 0.53 GB JPEG + 0.19 GB .npy | 0.61 GB (JPEG as served, PNG for the original block) |
| BD TOPO roads, buildings, water | 4.2 GB (the Haute-Garonne GeoJSON uncompressed) | 1.09 GB |
| ground layers, OSM | (per-area files) | 0.07 GB |
| **total** | **~26 GB, 0.53 MB/km²** | **14.1 GB, 0.29 MB/km²** (4,837 sectors, 49,500 km²) |

100 tiles of the original block had been downloaded a second time by the region fetch; the old reader always took the original
float32 file there, and so does the new layout (the second copies were dropped).

Verified: the small map built from the tiles (roads with `python -m roads build`, then `build_world.py`) gives road sector files
and all 1,152 chunk files and `far.bin` byte for byte identical to master; `places.json` now holds the place names of the map's
tiles and their neighbours instead of the whole region.

## Step 3: the road pipeline per tile (`tools/roads/tiled.py`)

No stage holds the whole network any more. The tiles are the sectors; four passes run over them, each tile reading the source
tiles and what earlier passes wrote for the tiles around it, and writing only what it owns
(`data/big/roads/<tag>/<pass>/<si>_<sj>.pkl`):

1. **align**: four rounds, like the colours of a 2 x 2 checkerboard (neighbours are never in the same round). A tile reads the
   sections within 1.6 km, cuts them where they cross a tile border (the two ends of a cut are one node of their own), and smooths
   its strokes. What a tile of an earlier round smoothed is fixed; a stroke continuing it is solved in the runs between fixed
   sections, pinned to the last 150 m of the fixed curve on each side, so it continues smoothly. A tile owns the section parts lying
   in it and the nodes that no tile of an earlier round touches.
2. **network**: the sections put back together; graph, links, junctions (only those within 400 m of the tile or of the ends of its
   links are built), crossings and tunnels. A link belongs to the tile holding its middle, a junction to the tile holding its
   centre; a tile loads its 3 x 3 neighbourhood and more when a link it needs is not whole there. Records refer to each other by
   key and owner tile.
3. **profile**: the existing checkerboard height solve, now one process per tile, the neighbourhood assembled from the records.
4. **surface**: lanes, the lane graph, road pieces and junction meshes of what the tile owns. Lane graph elements are numbered
   (tile code << 15) + rank, references to another tile's elements are resolved in a last short pass by what they belong to.

The world builder reads a sector's roads from the 3 x 3 tiles around it (`build.load_sector`). The whole-network pickle, the
alignment cache of the whole area and `inspect` / `gallery` / `compare_roads` on the whole network are gone (they work on the
tiles around a spot now).

**The smoothing order.** A tile cannot know the whole length of a stroke, and smoothing results depended on the order strokes
are smoothed in and on the end they are walked from (the solve is not direction-symmetric, and the stroke list order came from the
order of the BD TOPO files). The rule is now window-independent: by class, then length up to 1,600 m (the alignment halo), then the
smallest section id; each stroke walked along that section's digitised direction. Against master that alone moves 35 % of the
sections by more than 1 cm (p99 1.2 m, within the 1.2 - 2 m bound of the survey); it changes which road places a shared node, not
the quality (worst bound ratio 1.02 in both). With the same rule, the tiled build reproduces a whole-area build:

| small map, tiled vs whole area (same rules) | |
|---|---|
| road pieces, sections, junctions | 2,794 / 2,794, same sections, 1,315 / 1,315 junctions |
| plan, Hausdorff per piece | p90 0.003 m, p99 0.63 m, 3.4 % over 0.1 m (almost all within 200 m of a tile border) |
| heights, max per piece | p90 0.000 m, p99 0.017 m, max 0.12 m |
| junction planes | height p99 3 mm |

Small map, tiled build: `check_roads` OK (no terrain above a road or deck, every road end meets its junction, every lane leads
on: 12,224 elements, 2 without exit as on master); 2 invalid junctions (master 1; the whole-area build with the new rules has the
same 2); `check_gaps` 7 cm-level gaps (master 6, the same problem area; a 0.5 mm sliver the ground fill dropped was fixed).

Cost, b70s20 (42 tiles with the ring), from scratch, 5 workers: 65 s wall, 274 s CPU (6.5 s a tile), largest process 916 MB,
whole tree 3.9 GB (the whole-area build: 77 s wall with 9 workers, 155 s CPU, main process 1.0 GB growing with the area).
